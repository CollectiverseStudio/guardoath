"""Real service, exact provider transport and persisted ambiguity/race regressions."""
import base64
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from admin_cancellation_support import ACTOR, OTHER, TENANT, NOW, KEY, ProviderBoundary, request, review, service, snapshot, tenant
from admin_test_support import AdminTestDynamo
from guardoath import state_contracts
from guardoath.admin_cancellation_provider import CancellationProviderUnavailable, StripePeriodEndCancellation, digest
from guardoath.admin_cancellation_state import CancellationStateUnavailable, intent_hash
from guardoath.admin_subscription_cancellation import CancellationConflict, CancellationUnavailable, CancellationRateLimited
from guardoath.dynamodb_store import to_item
from guardoath.subscription_reconciliation import StripeBinding


@pytest.fixture(autouse=True)
def writer(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CANCELLATION_STATE", True)


def stored(db):
    return db.get_item(TableName="tenant_table", Key={"tenant_id":{"S":TENANT}})["Item"]


def test_single_dispatch_verified_linked_history_reopens_without_provider_or_tenant_change(tmp_path):
    path = tmp_path / "actions.sqlite3"
    db = AdminTestDynamo(path)
    actions, _, boundary = service(db)
    before = stored(db)
    prepared = review(actions)
    assert prepared["status"] == "reviewed" and prepared["scheduling_available"] is True
    payload = request(prepared)
    result = actions.apply(**payload)
    assert result["status"] == "verified" and result["integrity_status"] == "signed_verified"
    assert result["intent"]["intent_hash"] == result["outcome"]["intent_hash"]
    assert result["tenant_mutated"] is result["provider_state_refreshed"] is False
    assert stored(db) == before and len(boundary.posts) == 1
    count = len(boundary.calls)
    assert actions.apply(**payload) == result
    assert len(boundary.calls) == count
    encoded = json.dumps([prepared, result, db.audit_items()])
    for forbidden in ("rk_test_", "sub_cancel", "cus_cancel", "si_cancel", "price_plan", "acct_owner"):
        assert forbidden not in encoded
    reopened = AdminTestDynamo(path)
    recovered, _, _ = service(reopened, seed=False)
    recovered.provider = None
    state_contracts.ENABLE_NEW_CANCELLATION_STATE = False
    assert recovered.receipt(actor=ACTOR, tenant_id=TENANT, idempotency_key=payload["idempotency_key"]) == result
    assert stored(reopened) == before
    assert len([row for row in reopened.audit_items() if "financial_operation" in row]) == 3
    reopened.close()
    db.close()


def test_exact_fixed_provider_request_and_both_independent_verification_reads():
    actions, _, boundary = service()
    actions.apply(**request(review(actions)))
    assert [r.get_method() for r in boundary.calls] == ["GET", "GET", "GET", "GET", "POST", "GET", "GET"]
    post = boundary.posts[0]
    headers = {k.lower():v for k, v in post.header_items()}
    assert post.full_url == "https://api.stripe.com/v1/subscriptions/sub_cancel"
    assert post.data == b"cancel_at_period_end=true"
    assert headers["stripe-version"] == "2025-02-24.acacia"
    assert headers["idempotency-key"].startswith("guardoath-sc1-") and len(headers["idempotency-key"]) == 78
    assert base64.b64decode(headers["authorization"][6:]) == b"rk_test_synthetic_cancel_0123456789:"
    assert "stripe-account" not in headers and "stripe-context" not in headers
    assert headers["content-type"] == "application/x-www-form-urlencoded"


@pytest.mark.parametrize("change", ["owner", "tenant", "revision", "tamper", "expiry", "future"])
def test_preview_binding_and_expiry_refused_before_provider(change):
    time = [NOW]
    actions, db, boundary = service(clock=lambda:time[0])
    payload = request(review(actions))
    if change == "owner": payload["actor"] = OTHER
    if change == "tenant":
        payload["tenant_id"] = "usr-other"
        payload["confirmation"] = "SCHEDULE CANCELLATION usr-other"
    if change == "revision": payload["expected_revision"] = 4
    if change == "tamper":
        old = payload["preview_reference"]
        payload["preview_reference"] = old[:20] + ("a" if old[20] != "a" else "b") + old[21:]
    if change == "expiry": time[0] += 180
    if change == "future": time[0] -= 1
    before = len(boundary.calls)
    with pytest.raises(CancellationConflict): actions.apply(**payload)
    assert len(boundary.calls) == before and not db.audit_items()


@pytest.mark.parametrize("change", ["revision", "customer", "subscription", "same_revision_policy", "demo"])
def test_changed_authoritative_tenant_refuses_before_provider(change):
    actions, db, boundary = service()
    payload = request(review(actions))
    item = stored(db)
    if change == "revision": item["revision"]["N"] = "4"
    else:
        body = json.loads(item["record"]["S"])
        field, value = {"customer":("payer_id","cus_other"), "subscription":("stripe_subscription_id","sub_other"),
                       "same_revision_policy":("monthly_allowance",1), "demo":("demo",True)}[change]
        body[field] = value
        item["record"]["S"] = json.dumps(body)
    db.seed("tenant_table", item)
    count = len(boundary.calls)
    with pytest.raises((CancellationConflict, CancellationUnavailable)): actions.apply(**payload)
    assert len(boundary.calls) == count and not db.audit_items()


@pytest.mark.parametrize("field,value", [
    ("customer","cus_foreign"), ("id","sub_other"), ("livemode",True), ("status","trialing"),
    ("collection_method","send_invoice"), ("pause_collection",{}), ("schedule","sub_sched"),
    ("pending_update",{}), ("cancel_at_period_end",True), ("cancel_at",NOW+86400),
    ("current_period_start",True), ("current_period_end",True), ("current_period_end",NOW),
])
def test_unsupported_raw_provider_states_cannot_be_previewed(field,value):
    actions, db, boundary = service()
    boundary.value[field] = value
    with pytest.raises(CancellationUnavailable): review(actions)
    assert not boundary.posts and not db.audit_items()


@pytest.mark.parametrize("field", ["pause_collection", "schedule", "pending_update", "cancel_at", "cancel_at_period_end"])
def test_missing_provider_fields_are_not_treated_as_explicit_null(field):
    actions, _, boundary = service()
    del boundary.value[field]
    with pytest.raises(CancellationUnavailable): review(actions)
    assert not boundary.posts


@pytest.mark.parametrize("change", ["quantity", "item_id", "item_binding", "price", "price_mode", "has_more", "list_type", "period_start", "period_end"])
def test_item_plan_quantity_and_period_contracts_fail_closed(change):
    actions, _, boundary = service()
    item = boundary.value["items"]["data"][0]
    if change == "quantity": item["quantity"] = True
    if change == "item_id": item["id"] = "unsafe/si_1"
    if change == "item_binding": item["subscription"] = "sub_other"
    if change == "price": item["price"]["id"] = "price_other"
    if change == "price_mode": item["price"]["livemode"] = True
    if change == "has_more": boundary.value["items"]["has_more"] = True
    if change == "list_type": boundary.value["items"]["object"] = "subscription"
    if change == "period_start": item["current_period_start"] = NOW - 1
    if change == "period_end": item["current_period_end"] = NOW + 1
    with pytest.raises(CancellationUnavailable): review(actions)
    assert not boundary.posts


@pytest.mark.parametrize("change", ["period", "item", "quantity", "account", "price"])
def test_fresh_pre_dispatch_provider_drift_refuses_without_control_or_post(change):
    actions, db, boundary = service()
    payload = request(review(actions))
    if change == "period": boundary.value["current_period_end"] += 1
    if change == "item": boundary.value["items"]["data"][0]["id"] = "si_replaced"
    if change == "quantity": boundary.value["items"]["data"][0]["quantity"] = 2
    if change == "account": boundary.account["id"] = "acct_other"
    if change == "price": boundary.value["items"]["data"][0]["price"]["id"] = "price_other"
    with pytest.raises((CancellationConflict,CancellationUnavailable)): actions.apply(**payload)
    assert not boundary.posts and not db.audit_items()


def test_atomic_tenant_dispatch_check_catches_same_revision_edit_during_provider_read():
    actions, db, boundary = service()
    payload = request(review(actions))
    original = stored(db)
    def race(req):
        if req.full_url.endswith("/sub_cancel") and req.get_method() == "GET":
            item = stored(db)
            value = json.loads(item["record"]["S"])
            value["payer_id"] = "cus_other"
            item["record"]["S"] = json.dumps(value)
            db.seed("tenant_table", item)
    boundary.before = race
    result = actions.apply(**payload)
    assert result["status"] == "indeterminate" and not boundary.posts
    assert stored(db) != original and stored(db)["revision"] == original["revision"]
    assert actions.state.read("control", result["intent"]["target_fingerprint"])["state"] == "BLOCKED"


@pytest.mark.parametrize("failure", ["post_response", "verification", "outcome_write", "outcome_response", "finish_response", "dispatch_response"])
def test_lost_responses_recover_only_signed_history_and_never_dispatch_again(monkeypatch,failure):
    actions, db, boundary = service()
    payload = request(review(actions))
    original = stored(db)
    if failure == "post_response": boundary.lose_post_response = True
    if failure == "verification":
        def after(req, value):
            if boundary.posts and req.get_method() == "GET": raise TimeoutError("private verification error")
            return value
        boundary.after = after
    put = db.put_item
    def fail_put(**kw):
        value = json.loads(kw["Item"]["financial_operation"]["S"])
        if value["kind"] == "outcome" and failure == "outcome_write": raise TimeoutError("private audit failure")
        result = put(**kw)
        if ((value["kind"] == "outcome" and failure == "outcome_response") or
                (value["kind"] == "control" and value["state"] == "VERIFIED" and failure == "finish_response")):
            raise TimeoutError("private lost receipt")
        return result
    monkeypatch.setattr(db,"put_item",fail_put)
    if failure == "dispatch_response":
        transaction = db.transact_write_items
        def lose_dispatch(**kw):
            result = transaction(**kw)
            if "ConditionCheck" in kw["TransactItems"][0]: raise TimeoutError("Lost dispatch claim response")
            return result
        monkeypatch.setattr(db,"transact_write_items",lose_dispatch)
    result = actions.apply(**payload)
    expected = "verified" if failure in ("outcome_response","finish_response") else "indeterminate"
    assert result["status"] == expected
    count = len(boundary.calls)
    assert actions.apply(**payload) == result
    assert actions.receipt(actor=ACTOR,tenant_id=TENANT,idempotency_key=payload["idempotency_key"]) == result
    assert len(boundary.calls) == count
    assert len(boundary.posts) == (0 if failure == "dispatch_response" else 1)
    assert stored(db) == original
    if expected == "indeterminate":
        boundary.after = lambda req,value:value
        boundary.value["cancel_at_period_end"] = False
        boundary.value["cancel_at"] = None
        with pytest.raises(CancellationConflict): actions.apply(**request(review(actions),key="different-action-123456789"))
        assert len(boundary.posts) == (0 if failure == "dispatch_response" else 1)


def test_distinct_owners_and_aliases_compete_for_same_target_once(tmp_path):
    path = tmp_path / "competing.sqlite3"
    one, db1, boundary = service(AdminTestDynamo(path))
    db1.seed("tenant_table",to_item(tenant("usr-alias")))
    two, db2, _ = service(AdminTestDynamo(path),boundary=boundary,seed=False)
    a = request(review(one),key="competing-action-one-1234")
    b = request(review(two,actor=OTHER,tenant_id="usr-alias"),actor=OTHER,tenant_id="usr-alias",key="competing-action-two-1234")
    barrier = Barrier(2)
    def before(req):
        if req.full_url.endswith("/sub_cancel") and req.get_method() == "GET" and not boundary.posts:
            barrier.wait(timeout=5)
    boundary.before = before
    def run(actions,payload):
        try: return actions.apply(**payload)["status"]
        except (CancellationConflict,CancellationUnavailable): return "refused"
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(run,one,a),workers.submit(run,two,b)]
        results = [f.result(timeout=10) for f in futures]
    assert sorted(results) == ["refused","verified"]
    assert len(boundary.posts) == 1
    assert stored(db1) == to_item(tenant())
    db1.close(); db2.close()


def test_different_request_same_action_reference_conflicts_even_across_tenants():
    actions, db, boundary = service()
    payload = request(review(actions))
    assert actions.apply(**payload)["status"] == "verified"
    count = len(boundary.calls)
    for change in ({"reason":"Different reason for repeated action"},
                   {"tenant_id":"usr-other","confirmation":"SCHEDULE CANCELLATION usr-other"}):
        with pytest.raises(CancellationConflict): actions.apply(**{**payload,**change})
    assert len(boundary.calls) == count and len(boundary.posts) == 1


def test_known_verified_reversal_requires_new_review_and_increments_generation():
    actions, _, boundary = service()
    stale = request(review(actions), key="earlier-other-preview-123456789")
    first = actions.apply(**request(review(actions)))
    boundary.value["cancel_at_period_end"] = False
    boundary.value["cancel_at"] = None
    assert actions.receipt(actor=ACTOR,tenant_id=TENANT,idempotency_key="cancel-reference-123456789")["status"] == "verified"
    with pytest.raises(CancellationConflict): actions.apply(**stale)
    second = actions.apply(**request(review(actions),key="fresh-reviewed-action-123456789"))
    assert first["intent"]["generation"] == 1 and second["intent"]["generation"] == 2
    assert len(boundary.posts) == 2
    assert boundary.posts[0].get_header("Idempotency-key") != boundary.posts[1].get_header("Idempotency-key")


def test_key_rotation_cannot_make_target_control_disappear():
    actions, db, boundary = service()
    actions.apply(**request(review(actions)))
    boundary.value["cancel_at_period_end"] = False
    boundary.value["cancel_at"] = None
    rotated, _, _ = service(db,boundary=boundary,key=b"different-synthetic-signing-key-0123456789",seed=False)
    assert rotated.provider.target_id("sub_cancel") == actions.provider.target_id("sub_cancel")
    with pytest.raises(CancellationUnavailable): rotated.apply(**request(review(rotated),key="rotated-key-action-123456789"))
    assert len(boundary.posts) == 1


@pytest.mark.parametrize("kind", ["intent","outcome","control"])
def test_corrupt_persisted_envelopes_fail_closed(kind):
    actions, db, boundary = service()
    payload = request(review(actions))
    result = actions.apply(**payload)
    identifier = result["intent"]["target_fingerprint"] if kind == "control" else result["intent"]["action_id"]
    row = actions.state.row_id(kind,identifier)
    item = db.get_item(TableName="audit_table",Key={"event_id":{"S":row}})["Item"]
    value = json.loads(item["financial_operation"]["S"])
    value["generation"] += 1
    item["financial_operation"]["S"] = json.dumps(value,sort_keys=True,separators=(",",":"))
    db.seed("audit_table",item)
    if kind == "control":
        boundary.value["cancel_at_period_end"] = False; boundary.value["cancel_at"] = None
        with pytest.raises(CancellationUnavailable): actions.apply(**request(review(actions),key="other-key-123456789"))
    else:
        with pytest.raises(CancellationUnavailable): actions.receipt(actor=ACTOR,tenant_id=TENANT,idempotency_key=payload["idempotency_key"])
    assert len(boundary.posts) == 1


def test_preview_budget_is_bounded_without_claiming_distributed_limiting():
    actions, db, boundary = service()
    for _ in range(20): review(actions)
    with pytest.raises(CancellationRateLimited): review(actions)
    assert len(boundary.calls) == 40 and not db.audit_items()

def test_lost_atomic_reservation_response_preserves_recoverable_intent_without_dispatch():
    actions, db, boundary = service()
    payload = request(review(actions))
    db.lose_transaction_response = True
    with pytest.raises(CancellationUnavailable): actions.apply(**payload)
    history = actions.receipt(actor=ACTOR,tenant_id=TENANT,idempotency_key=payload["idempotency_key"])
    assert history["status"] == "indeterminate" and history["intent"] is not None and history["outcome"] is None
    assert actions.apply(**payload) == history
    assert not boundary.posts
    assert actions.state.read("control",history["intent"]["target_fingerprint"])["state"] == "RESERVED"


def test_same_action_reference_two_different_subscriptions_cannot_strand_losing_target(tmp_path,monkeypatch):
    path = tmp_path / "shared-reference.sqlite3"
    one, db1, provider1 = service(AdminTestDynamo(path))
    second_record = tenant("usr-distinct").model_copy(update={"payer_id":"cus_distinct","stripe_subscription_id":"sub_distinct"})
    db1.seed("tenant_table",to_item(second_record))
    provider2 = ProviderBoundary()
    provider2.value.update(id="sub_distinct",customer="cus_distinct")
    provider2.value["items"]["data"][0].update(id="si_distinct",subscription="sub_distinct")
    two, db2, _ = service(AdminTestDynamo(path),boundary=provider2,seed=False)
    first = request(review(one))
    second = request(review(two,tenant_id="usr-distinct"),tenant_id="usr-distinct")
    barrier = Barrier(2)
    for actions in (one,two):
        original = actions.state.reserve
        def reserve(fields,*,expected_control_hash,original=original):
            barrier.wait(timeout=5)
            return original(fields,expected_control_hash=expected_control_hash)
        monkeypatch.setattr(actions.state,"reserve",reserve)
    def run(actions,payload):
        try: return actions.apply(**payload)["status"]
        except (CancellationConflict,CancellationUnavailable): return "refused"
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures=[workers.submit(run,one,first),workers.submit(run,two,second)]
        assert sorted(f.result(timeout=10) for f in futures) == ["refused","verified"]
    controls=[json.loads(row["financial_operation"]["S"]) for row in db1.audit_items()
              if json.loads(row["financial_operation"]["S"])["kind"] == "control"]
    assert len(controls) == 1
    assert len(provider1.posts)+len(provider2.posts) == 1
    loser = two if provider1.posts else one
    loser_sub = "sub_distinct" if provider1.posts else "sub_cancel"
    assert loser.state.read("control",loser.provider.target_id(loser_sub)) is None
    db1.close(); db2.close()


def test_operation_budget_refuses_before_reservation_and_preserves_original_privacy_deadline():
    actions, db, boundary = service()
    payload = request(review(actions))
    with pytest.raises(CancellationUnavailable): actions.apply(**payload,execution_budget_seconds=3.0)
    assert not boundary.posts and not db.audit_items()
    assert actions.provider._timeout == 1.0


def test_exhausted_provider_deadline_sends_no_request():
    actions, _, boundary = service()
    import time
    before = len(boundary.calls)
    with pytest.raises(CancellationProviderUnavailable):
        actions.provider.inspect(StripeBinding(TENANT,"cus_cancel","sub_cancel"),scheduled=False,now=NOW,deadline=time.monotonic()-1)
    assert len(boundary.calls) == before


def test_indeterminate_claim_survives_days_and_current_provider_state_cannot_release_it():
    now = [NOW]
    actions, _, boundary = service(clock=lambda:now[0])
    boundary.lose_post_response = True
    first = actions.apply(**request(review(actions)))
    assert first["status"] == "indeterminate"
    now[0] += 86400*3
    boundary.value.update(cancel_at_period_end=False,cancel_at=None,
        current_period_start=now[0]-100,current_period_end=now[0]+86400*10)
    later = review(actions)
    with pytest.raises(CancellationConflict): actions.apply(**request(later,key="three-days-later-123456789"))
    assert len(boundary.posts) == 1


def test_backwards_outcome_clock_cannot_insert_permanently_invalid_record(monkeypatch):
    actions, db, _ = service()
    captured = {}
    original = actions.state.outcome
    def outcome(intent,status,now):
        captured["intent"] = intent
        return original(intent,status,intent["created_at"]-1)
    monkeypatch.setattr(actions.state,"outcome",outcome)
    result = actions.apply(**request(review(actions)))
    assert result["status"] == "indeterminate" and result["outcome"] is None
    assert actions.state.read("outcome",captured["intent"]["action_id"]) is None


@pytest.mark.parametrize("hash_field",["update_hash","verification_hash"])
def test_indeterminate_signed_schema_rejects_partial_success_evidence(hash_field):
    actions, _, boundary = service()
    boundary.lose_post_response=True
    result=actions.apply(**request(review(actions)))
    intent,outcome=actions.state.history(result["intent"]["action_id"])
    malformed={**outcome,hash_field:intent["provider_hash"]}
    with pytest.raises(CancellationStateUnavailable): actions.state._seal(malformed)


def test_budget_exhaustion_during_dispatch_claim_prevents_post_and_retains_history(monkeypatch):
    import time
    monotonic=[500.0]
    monkeypatch.setattr(time,"monotonic",lambda:monotonic[0])
    actions,db,boundary=service()
    payload=request(review(actions))
    original=db.transact_write_items
    def slow_claim(**kwargs):
        result=original(**kwargs)
        if "ConditionCheck" in kwargs["TransactItems"][0]: monotonic[0]+=5
        return result
    monkeypatch.setattr(db,"transact_write_items",slow_claim)
    result=actions.apply(**payload)
    assert result["status"]=="indeterminate" and result["intent"] is not None
    assert not boundary.posts
    assert actions.apply(**payload)==result
    assert actions.state.read("control",result["intent"]["target_fingerprint"])["state"]=="BLOCKED"
