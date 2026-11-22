"""Durable admission/dispatch races and failure recovery; no AWS/model calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import json
import sqlite3
from threading import Barrier, Lock
from uuid import uuid4

import pytest

from guardoath.invocations.contracts import CheckEvidence, InvocationReceipt, ReportedUsage, TargetSnapshot
from guardoath.invocations.store import (
    DispatchQuota, DynamoDBInvocationStore, InvocationConflict, InvocationQuotaExceeded,
    InvocationStoreUnavailable, SQLiteInvocationStore,
)
from guardoath.models import Action


KEY = b"synthetic-invocation-store-key-32-bytes-minimum"
NOW = "2026-11-22T12:00:00Z"


def initial(*, application="customer-a", request="request-1", connection="primary", version="v1", created=NOW):
    return InvocationReceipt(
        application_id=application, interaction_id=uuid4().hex, request_id=request,
        created_at=created, updated_at=created, status="admitted",
        target=TargetSnapshot(connection_id=connection, connection_version=version, provider="aws_bedrock",
                              region="us-east-1", model_id="fast", provider_model_id="amazon.nova-micro-v1:0"),
        policy_revision=1, policy_digest="b" * 64, catalog_version="guarded-models-1",
    )


def changed(receipt, **updates):
    return InvocationReceipt.model_validate({**receipt.model_dump(), **updates})


def evidence(decision=Action.ALLOW, *, simulated=False, complete=True):
    return CheckEvidence(check_id=uuid4().hex, decision=decision,
        evaluation_status="complete" if complete else "incomplete", simulated=simulated,
        checker_ids=("secrets", "pii"), finding_ids=())


def admitted(store, **kwargs):
    receipt = initial(**kwargs)
    fingerprint = store.make_fingerprint(receipt.application_id, b'{"message":"synthetic test"}')
    return store.admit(receipt, fingerprint).receipt


def checked(store, **kwargs):
    receipt = admitted(store, **kwargs)
    return store.advance(receipt, changed(receipt, status="input_checked", input_check=evidence()))


def dispatch(store, receipt, *, calls=10, tokens=100, reserve=5):
    return store.reserve_dispatch(receipt, changed(receipt, status="dispatch_intent", dispatch_state="intent_recorded",
        reserved_output_tokens=reserve, quota_period="2026-11"), DispatchQuota(calls, tokens, "2026-11"))


def observed(store, receipt):
    return store.advance(receipt, changed(receipt, status="provider_result", dispatch_state="response_observed",
        provider_request_id="provider-request", provider_response_id="provider-response",
        observed_model=receipt.target.provider_model_id, usage=ReportedUsage(input_tokens=12, output_tokens=3)))


class TransactionCancelled(Exception):
    def __init__(self, conditions):
        self.response = {"Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [{"Code": "None" if passed else "ConditionalCheckFailed"} for passed in conditions]}


class AtomicDynamo:
    """Independent transactional storage double, including real thread races."""

    def __init__(self):
        self.items, self.lock, self.calls, self.fail = {}, Lock(), [], None

    def get_item(self, **params):
        assert params["ConsistentRead"] is True
        key = (params["Key"]["pk"]["S"], params["Key"]["sk"]["S"])
        with self.lock:
            item = self.items.get(key)
            return {"Item": deepcopy(item)} if item is not None else {}

    def query(self, **params):
        assert params["ConsistentRead"] is True
        values = params["ExpressionAttributeValues"]
        pk, start, end = (values[name]["S"] for name in (":pk", ":start", ":end"))
        with self.lock:
            items = [deepcopy(item) for (owner, key), item in self.items.items() if owner == pk and start <= key <= end]
        items.sort(key=lambda item: item["sk"]["S"], reverse=not params["ScanIndexForward"])
        if "ExclusiveStartKey" in params:
            position = params["ExclusiveStartKey"]["sk"]["S"]
            items = [item for item in items if (item["sk"]["S"] > position if params["ScanIndexForward"] else item["sk"]["S"] < position)]
        return {"Items": items[:params["Limit"]]}

    def transact_write_items(self, **params):
        assert len(params["ClientRequestToken"]) == 32
        with self.lock:
            self.calls.append(deepcopy(params))
            failure, self.fail = self.fail, None
            if failure == "before":
                raise OSError("provider body synthetic-private-canary")
            changes, conditions = [], []
            for transaction in params["TransactItems"]:
                put = transaction["Put"]
                item = put["Item"]
                key = (item["pk"]["S"], item["sk"]["S"])
                old = self.items.get(key)
                if put["ConditionExpression"] == "attribute_not_exists(#pk)":
                    conditions.append(old is None)
                else:
                    assert put["ConditionExpression"] == "#document = :previous"
                    conditions.append(old is not None and old["document"] == put["ExpressionAttributeValues"][":previous"])
                changes.append((key, deepcopy(item)))
            if not all(conditions):
                raise TransactionCancelled(conditions)
            for key, item in changes:
                self.items[key] = item
            if failure == "after":
                raise TimeoutError("provider body synthetic-private-canary")
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}


@dataclass
class Harness:
    first: object
    second: object
    reopen: object
    documents: object
    replace: object


@pytest.fixture(params=["sqlite", "dynamodb"])
def stores(request, tmp_path):
    if request.param == "sqlite":
        path = tmp_path / "invocations.sqlite3"
        def create(key=KEY):
            return SQLiteInvocationStore(path, key)
        def documents():
            with sqlite3.connect(path) as db:
                return {(pk, sk): raw for pk, sk, raw in db.execute("SELECT pk, sk, document FROM invocation_items")}
        def replace(key, raw):
            with sqlite3.connect(path) as db:
                if raw is None:
                    db.execute("DELETE FROM invocation_items WHERE pk=? AND sk=?", key)
                else:
                    db.execute("INSERT OR REPLACE INTO invocation_items (pk, sk, document) VALUES (?, ?, ?)", (*key, raw))
    else:
        client = AtomicDynamo()
        def create(key=KEY):
            return DynamoDBInvocationStore("invocation-metadata", client, key)
        def documents():
            with client.lock:
                return {key: value["document"]["S"] for key, value in client.items.items()}
        def replace(key, raw):
            with client.lock:
                if raw is None:
                    client.items.pop(key, None)
                else:
                    client.items[key] = {"pk": {"S": key[0]}, "sk": {"S": key[1]}, "document": {"S": raw}}
    return Harness(create(), create(), create, documents, replace)


def test_admission_survives_restart_and_replays_original_policy(stores):
    first = initial()
    fingerprint = stores.first.make_fingerprint(first.application_id, b"the canonical request")
    accepted = stores.first.admit(first, fingerprint)
    assert accepted.created and accepted.receipt.sequence == 1 and accepted.receipt.event_digest != "0" * 64
    retry = changed(initial(), policy_revision=99, policy_digest="a" * 64)
    replay = stores.reopen().admit(retry, fingerprint)
    assert replay == type(replay)(accepted.receipt, False)
    assert stores.second.lookup_request(first.application_id, first.request_id) == accepted.receipt
    assert stores.second.lookup_request("customer-b", first.request_id) is None
    assert stores.second.get("customer-b", first.interaction_id) is None
    with pytest.raises(InvocationConflict):
        stores.second.admit(retry, "f" * 64)


def test_two_instances_admit_one_request_once(stores):
    barrier = Barrier(2)
    def run(store):
        receipt = initial()
        barrier.wait()
        return store.admit(receipt, "f" * 64)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, (stores.first, stores.second)))
    assert sum(result.created for result in outcomes) == 1
    assert outcomes[0].receipt == outcomes[1].receipt
    assert len(stores.documents()) == 4


@pytest.mark.parametrize("call_limit,expected_successes", [(1, 1), (2, 2)])
def test_two_instances_atomically_cas_head_event_and_shared_budget(stores, call_limit, expected_successes):
    receipts = [checked(stores.first, request="request-a"), checked(stores.second, request="request-b")]
    barrier, lock, arrivals = Barrier(2), Lock(), []
    # Both processes read the same budget before either transaction commits.
    for store in (stores.first, stores.second):
        original = store._write
        def contested(changes, original=original):
            with lock:
                arrivals.append(1)
                wait = len(arrivals) <= 2
            if wait:
                barrier.wait(timeout=5)
            return original(changes)
        store._write = contested
    def run(pair):
        store, receipt = pair
        try:
            return dispatch(store, receipt, calls=call_limit)
        except InvocationQuotaExceeded:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, zip((stores.first, stores.second), receipts)))
    assert sum(item is not None for item in results) == expected_successes
    budgets = [json.loads(raw) for (_, key), raw in stores.documents().items() if key.startswith("B#")]
    assert len(budgets) == 1
    assert budgets[0]["calls"] == expected_successes
    assert budgets[0]["output_tokens"] == expected_successes * 5
    for receipt, result in zip(receipts, results):
        current = stores.reopen().get(receipt.application_id, receipt.interaction_id)
        assert current.status == ("dispatch_intent" if result else "input_checked")


def test_same_receipt_cannot_mint_two_dispatch_permits(stores):
    receipt = checked(stores.first)
    sent = dispatch(stores.first, receipt)
    with pytest.raises(InvocationConflict):
        dispatch(stores.second, receipt)
    with pytest.raises(InvocationConflict):
        dispatch(stores.second, sent)
    assert stores.reopen().get(receipt.application_id, receipt.interaction_id) == sent


@pytest.mark.parametrize("calls,tokens", [(1, 100), (10, 9)])
def test_connector_rotation_does_not_reset_held_unknown_reservations(stores, calls, tokens):
    first = dispatch(stores.first, checked(stores.first), calls=calls, tokens=tokens)
    unknown = stores.first.advance(first, changed(first, status="outcome_unknown", dispatch_state="outcome_unknown",
                                                 reason_code="provider_timeout"))
    second = checked(stores.second, request="new-request", version="rotated-v2")
    with pytest.raises(InvocationQuotaExceeded):
        dispatch(stores.second, second, calls=calls, tokens=tokens)
    assert stores.reopen().get(unknown.application_id, unknown.interaction_id) == unknown
    assert stores.second.get(second.application_id, second.interaction_id) == second
    other = checked(stores.second, application="customer-b")
    assert dispatch(stores.second, other, calls=calls, tokens=tokens).status == "dispatch_intent"


def test_signed_receipts_append_events_and_never_retain_content(stores):
    prompt = b'{"message":"synthetic-secret-prompt-canary"}'
    original = initial()
    current = stores.first.admit(original, stores.first.make_fingerprint(original.application_id, prompt)).receipt
    current = stores.first.advance(current, changed(current, status="input_checked", input_check=evidence()))
    current = observed(stores.first, dispatch(stores.first, current))
    final = stores.first.advance(current, changed(current, status="completed", output_check=evidence()))
    assert final.sequence == 5 and not final.response_retained
    assert stores.reopen().get(final.application_id, final.interaction_id) == final
    events = [json.loads(raw) for (_, key), raw in stores.documents().items() if key.startswith("E#")]
    assert len(events) == 5
    events.sort(key=lambda event: event["receipt"]["sequence"])
    assert events[0]["previous_digest"] == "0" * 64
    assert all(right["previous_digest"] == left["receipt"]["event_digest"] for left, right in zip(events, events[1:]))
    raw = "\n".join(stores.documents().values())
    assert "synthetic-secret-prompt-canary" not in raw
    assert '"message"' not in raw and '"response"' not in raw and '"content"' not in raw
    assert KEY.decode() not in raw
    with pytest.raises(InvocationConflict):
        stores.second.advance(final, changed(final, status="outcome_unknown", dispatch_state="outcome_unknown"))


@pytest.mark.parametrize("changes", [
    {"application_id": "customer-b"}, {"request_id": "other-request"}, {"interaction_id": "e" * 32},
    {"created_at": "2026-11-21T12:00:00Z"}, {"policy_revision": 2}, {"policy_digest": "d" * 64},
    {"catalog_version": "different"}, {"sequence": 2}, {"event_digest": "f" * 64},
    {"updated_at": "2026-11-21T12:00:00Z"},
])
def test_admission_bindings_and_cas_version_are_immutable(stores, changes):
    receipt = admitted(stores.first)
    after = receipt.model_copy(update={"status": "input_checked", "input_check": evidence(), **changes})
    with pytest.raises(InvocationConflict):
        stores.first.advance(receipt, after)
    assert stores.second.get(receipt.application_id, receipt.interaction_id) == receipt


@pytest.mark.parametrize("check", [evidence(Action.BLOCK), evidence(simulated=True), evidence(complete=False)])
def test_blocked_simulated_incomplete_input_cannot_reserve(stores, check):
    first = admitted(stores.first)
    receipt = stores.first.advance(first, changed(first, status="input_checked", input_check=check))
    with pytest.raises(InvocationConflict):
        dispatch(stores.first, receipt)
    assert not any(key.startswith("B#") for _, key in stores.documents())


def test_plain_advance_cannot_bypass_dispatch_reservation(stores):
    receipt = checked(stores.first)
    candidate = changed(receipt, status="dispatch_intent", dispatch_state="intent_recorded",
                        reserved_output_tokens=5, quota_period="2026-11")
    with pytest.raises(InvocationConflict):
        stores.first.advance(receipt, candidate)
    assert stores.second.get(receipt.application_id, receipt.interaction_id) == receipt


def test_unknown_can_record_initial_provider_metadata_but_cannot_replace_observed_metadata(stores):
    intent = dispatch(stores.first, checked(stores.first))
    unknown = stores.first.advance(intent, changed(intent, status="outcome_unknown", dispatch_state="outcome_unknown",
        provider_request_id="request-observed-before-timeout", provider_response_id="response-started",
        observed_model=intent.target.provider_model_id, observed_service_tier="default",
        usage=ReportedUsage(input_tokens=12), reason_code="provider_timeout"))
    assert stores.second.get(unknown.application_id, unknown.interaction_id) == unknown
    provider = observed(stores.first, dispatch(stores.first, checked(stores.first, request="second")))
    with pytest.raises(InvocationConflict):
        stores.first.advance(provider, changed(provider, status="outcome_unknown", dispatch_state="outcome_unknown",
                                               provider_request_id="changed", reason_code="deadline_exceeded"))
    preserved = stores.first.advance(provider, changed(provider, status="outcome_unknown", dispatch_state="outcome_unknown",
                                                       reason_code="deadline_exceeded"))
    assert preserved.usage == provider.usage and preserved.provider_request_id == provider.provider_request_id
    assert stores.second.get(preserved.application_id, preserved.interaction_id) == preserved


@pytest.mark.parametrize("reason", ["request_invalid", "model_not_allowed", "configuration_unavailable"])
def test_known_local_preflight_refusal_is_not_a_provider_response_and_keeps_reservation(stores, reason):
    intent = dispatch(stores.first, checked(stores.first), calls=1)
    refused = stores.first.advance(intent, changed(intent, status="provider_rejected", dispatch_state="not_dispatched",
                                                  reason_code=reason))
    assert refused.reserved_output_tokens == 5 and refused.usage is None
    assert stores.second.get(refused.application_id, refused.interaction_id) == refused
    with pytest.raises(InvocationQuotaExceeded):
        dispatch(stores.second, checked(stores.second, request="second"), calls=1)


@pytest.mark.parametrize("updates", [
    {"reason_code": "arbitrary_failure"}, {"provider_request_id": "looks-like-a-remote-request"},
    {"usage": ReportedUsage(input_tokens=1)}, {"dispatch_state": "response_observed"},
])
def test_local_preflight_refusal_rejects_remote_claims_or_nonlocal_reason(stores, updates):
    intent = dispatch(stores.first, checked(stores.first))
    fields = {"status": "provider_rejected", "dispatch_state": "not_dispatched", "reason_code": "request_invalid", **updates}
    with pytest.raises(InvocationConflict):
        stores.first.advance(intent, changed(intent, **fields))
    assert stores.second.get(intent.application_id, intent.interaction_id) == intent


@pytest.mark.parametrize("which", ["head", "event", "event_missing", "mapping", "index", "budget"])
def test_corrupt_or_missing_persistence_fails_closed(stores, which):
    receipt = dispatch(stores.first, checked(stores.first))
    prefix = {"head": "I#", "event": "E#", "event_missing": "E#", "mapping": "R#", "index": "L#", "budget": "B#"}[which]
    key = next(key for key in stores.documents() if key[1].startswith(prefix))
    stores.replace(key, None if which == "event_missing" else '{"synthetic_corruption":true}')
    with pytest.raises(InvocationStoreUnavailable):
        if which == "index":
            stores.second.recent(receipt.application_id)
        elif which == "budget":
            another = checked(stores.second, request="another")
            dispatch(stores.second, another)
        else:
            stores.second.get(receipt.application_id, receipt.interaction_id)


def test_replaced_key_or_tenant_swapped_head_cannot_validate(stores):
    receipt = admitted(stores.first)
    with pytest.raises(InvocationStoreUnavailable):
        stores.reopen(b"different-signing-key-at-least-32-bytes").get(receipt.application_id, receipt.interaction_id)
    key = ("T#customer-a", "I#" + receipt.interaction_id)
    stores.replace(("T#customer-b", key[1]), stores.documents()[key])
    with pytest.raises(InvocationStoreUnavailable):
        stores.second.get("customer-b", receipt.interaction_id)


@pytest.mark.parametrize("prefix", ["E#", "I#", "R#", "L#", "B#"])
def test_well_formed_metadata_tampering_is_detected(stores, prefix):
    receipt = dispatch(stores.first, checked(stores.first))
    key = next(key for key in stores.documents() if key[1].startswith(prefix))
    value = json.loads(stores.documents()[key])
    if prefix in {"E#", "I#"}:
        value["receipt"]["policy_revision"] += 1
    elif prefix == "R#":
        value["fingerprint"] = "e" * 64
    elif prefix == "L#":
        value["created_at"] = "2026-11-22T13:00:00Z"
    else:
        value["output_tokens"] += 1
    stores.replace(key, json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    with pytest.raises(InvocationStoreUnavailable):
        if prefix == "L#":
            stores.second.recent(receipt.application_id)
        elif prefix == "B#":
            dispatch(stores.second, checked(stores.second, request="another"))
        else:
            stores.second.get(receipt.application_id, receipt.interaction_id)


@pytest.mark.parametrize("signature", ["\u2603", None, 7, "a" * 63])
def test_malformed_signature_returns_safe_persistence_failure(stores, signature):
    receipt = admitted(stores.first)
    key = next(key for key in stores.documents() if key[1].startswith("R#"))
    value = json.loads(stores.documents()[key])
    value["signature"] = signature
    stores.replace(key, json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    with pytest.raises(InvocationStoreUnavailable):
        stores.second.lookup_request(receipt.application_id, receipt.request_id)


def test_recent_uses_timestamp_index_and_same_tenant_chain(stores):
    later = admitted(stores.first, request="later", created="2026-11-22T13:00:00Z")
    earlier = admitted(stores.first, request="earlier")
    admitted(stores.first, application="customer-b", request="unrelated", created="2026-11-22T14:00:00Z")
    assert stores.second.recent("customer-a") == (later, earlier)
    assert stores.second.recent("customer-a", 1) == (later,)
    assert stores.second.recent("missing") == ()
    with pytest.raises(InvocationConflict):
        stores.first.recent("customer-a", 21)


def test_fingerprints_are_stable_secret_and_tenant_separated(stores):
    assert stores.first.make_fingerprint("customer-a", b"same") == stores.second.make_fingerprint("customer-a", b"same")
    assert stores.first.make_fingerprint("customer-a", b"same") != stores.first.make_fingerprint("customer-b", b"same")
    assert stores.first.make_fingerprint("customer-a", b"same") != stores.first.make_fingerprint("customer-a", b"changed")
    assert stores.first.make_fingerprint("customer-a", b"same") != stores.reopen(b"another-key-with-at-least-32-bytes!").make_fingerprint("customer-a", b"same")


def test_free_text_provider_metadata_and_unvalidated_copy_are_refused(stores):
    receipt = dispatch(stores.first, checked(stores.first))
    bad = receipt.model_copy(update={"status": "provider_result", "dispatch_state": "response_observed",
                                      "provider_response_id": "private provider error body"})
    with pytest.raises(InvocationConflict) as error:
        stores.first.advance(receipt, bad)
    assert "private provider" not in str(error.value)
    assert stores.second.get(receipt.application_id, receipt.interaction_id) == receipt


@pytest.mark.parametrize("phase", ["before", "after"])
def test_ambiguous_dynamo_admission_raises_without_authority(phase):
    client = AtomicDynamo()
    store = DynamoDBInvocationStore("invocation-metadata", client, KEY)
    receipt = initial()
    client.fail = phase
    with pytest.raises(InvocationStoreUnavailable) as error:
        store.admit(receipt, "a" * 64)
    assert "canary" not in str(error.value)
    recovered = store.lookup_request(receipt.application_id, receipt.request_id)
    assert (recovered is not None) == (phase == "after")
    if phase == "after":
        assert store.admit(receipt, "a" * 64).created is False


def test_ambiguous_dynamo_dispatch_is_readable_but_cannot_dispatch_again():
    client = AtomicDynamo()
    first = DynamoDBInvocationStore("invocation-metadata", client, KEY)
    second = DynamoDBInvocationStore("invocation-metadata", client, KEY)
    receipt = checked(first)
    client.fail = "after"
    with pytest.raises(InvocationStoreUnavailable):
        dispatch(first, receipt)
    recovered = second.lookup_request(receipt.application_id, receipt.request_id)
    assert recovered.status == "dispatch_intent" and recovered.reserved_output_tokens == 5
    with pytest.raises(InvocationConflict):
        dispatch(second, receipt)
    with pytest.raises(InvocationConflict):
        dispatch(second, recovered)
    budget = next(json.loads(value["document"]["S"]) for (_, key), value in client.items.items() if key.startswith("B#"))
    assert budget["calls"] == 1 and budget["output_tokens"] == 5


def test_ambiguous_terminal_write_never_reports_success_to_original_caller():
    client = AtomicDynamo()
    store = DynamoDBInvocationStore("invocation-metadata", client, KEY)
    receipt = observed(store, dispatch(store, checked(store)))
    client.fail = "after"
    with pytest.raises(InvocationStoreUnavailable):
        store.advance(receipt, changed(receipt, status="completed", output_check=evidence()))
    recovered = store.lookup_request(receipt.application_id, receipt.request_id)
    assert recovered.status == "completed" and recovered.response_retained is False


def test_dynamodb_requests_match_real_api_shape_without_network():
    boto3 = pytest.importorskip("boto3")
    from botocore.stub import Stubber
    client = boto3.client("dynamodb", region_name="us-east-1", aws_access_key_id="synthetic",
                          aws_secret_access_key="synthetic", endpoint_url="https://dynamodb.invalid")
    fake = AtomicDynamo()
    store = DynamoDBInvocationStore("invocation-metadata", fake, KEY)
    receipt = admitted(store)
    transaction = fake.calls[0]
    # Stubber validates the SDK input/output model. No AWS credential discovery,
    # network request or DynamoDB resource creation is performed.
    with Stubber(client) as stub:
        stub.add_response("transact_write_items", {}, transaction)
        client.transact_write_items(**transaction)
        stub.add_response("get_item", {"Item": fake.items[("T#customer-a", "I#" + receipt.interaction_id)]},
            {"TableName": "invocation-metadata", "Key": {"pk": {"S": "T#customer-a"},
                "sk": {"S": "I#" + receipt.interaction_id}}, "ConsistentRead": True})
        DynamoDBInvocationStore("invocation-metadata", client, KEY)._read("T#customer-a", "I#" + receipt.interaction_id)


@pytest.mark.parametrize("quota", [(True, 100, "2026-11"), (1, 0, "2026-11"), (1, 100, "2026-13"), (1, 100, "0000-01")])
def test_trusted_quota_requires_bounded_integers_and_calendar_month(quota):
    with pytest.raises(ValueError):
        DispatchQuota(*quota)


def test_persistence_requires_stable_key_and_file(tmp_path):
    with pytest.raises(ValueError):
        SQLiteInvocationStore(tmp_path / "store.db", b"short")
    with pytest.raises(ValueError):
        SQLiteInvocationStore(":memory:", KEY)
    with pytest.raises(ValueError):
        DynamoDBInvocationStore("invocation-metadata", object(), KEY)
