"""Durable catalog request/worker authority with genuine signed SQLite reopen.

Only DynamoDB transport is synthetic. The production validators, signatures,
transaction requests, immutable rows and full-document CAS execute unchanged.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier

import pytest

from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
from test_catalog_source_release import export_plan, PROPOSAL, SOURCE
from guardoath import state_contracts
from guardoath.admin_catalog_publication_state import (
    CATALOG_ID, CONTRACT, EMPTY_CONTROL_HASH, MAX_DOCUMENT_BYTES,
    DynamoCatalogPublicationState, PublicationConflict, PublicationUnavailable,
    document_hash,
)

ACTOR = "12345678-1234-1234-1234-123456789012"
OTHER = "87654321-4321-4321-4321-210987654321"
KEY = ADMIN_BROWSER_AUDIT_KEY
TABLE = "admin_audit"
NOW = 1795500000


def fields(*, actor=ACTOR, reference="publish-reference-123456", plan=None):
    plan = deepcopy(plan or export_plan())
    return {
        "request_id": DynamoCatalogPublicationState.request_id(actor, reference),
        "actor_sub": actor, "review_id": plan["review_id"], "draft_id": plan["draft_id"],
        "review_sha256": "a" * 64, "draft_sha256": "b" * 64,
        "base_source_sha": "c" * 40, "operation": "publish_metadata",
        "registry_version": plan["registry_version"],
        "candidate_registry_version": plan["candidate_registry_version"],
        "baseline_sha256": plan["reviewed_source_sha256"],
        "candidate_metadata_sha256": plan["candidate_metadata_sha256"],
        "target": deepcopy(plan["target"]), "plan": plan,
        "plan_sha256": document_hash(plan), "reason": "Clarify the published rule description",
        "created_at": NOW, "request_hash": document_hash({"actor": actor, "reference": reference}),
    }


def prepared_fields(intent, **changes):
    return {
        "base_source_sha": intent["base_source_sha"], "candidate_source_sha": "d" * 40,
        "history_sha256": "e" * 64,
        "candidate_metadata_sha256": intent["candidate_metadata_sha256"],
        "candidate_registry_version": intent["candidate_registry_version"],
        "recorded_at": NOW + 10, **changes,
    }


def make_state(db=None, *, key=KEY):
    db = db or AdminTestDynamo()
    return DynamoCatalogPublicationState(db, TABLE, key), db


def rows(db):
    return [json.loads(row[0]) for row in db.connection.execute(
        "SELECT body FROM items WHERE table_name=? ORDER BY item_key", (TABLE,))]


def record(db, kind, identifier):
    return db.get_item(TableName=TABLE,
        Key={"event_id": {"S": DynamoCatalogPublicationState.row_id(kind, identifier)}})["Item"]


@pytest.fixture
def writer(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", True)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", True)


def test_complete_signed_lifecycle_reopens_with_no_remote_publication_claim(tmp_path, writer):
    path = tmp_path / "audit.sqlite"
    state, db = make_state(AdminTestDynamo(path))
    inputs = fields()
    assert state.history(inputs["request_id"]) == (None, None)
    assert state.read_control() is None
    intent = state.reserve(inputs, EMPTY_CONTROL_HASH)
    assert intent["kind"] == "intent" and intent["contract"] == CONTRACT
    assert len(rows(db)) == 2 and db.transactions == 1
    control = state.read_control()
    assert control["state"] == "REQUESTED" and control["revision"] == 1
    assert control["catalog_id"] == CATALOG_ID
    assert control["intent_hash"] == document_hash(intent)
    reopened, second = make_state(AdminTestDynamo(path))
    assert reopened.read_request(intent["request_id"]) == intent
    assert reopened.history(intent["request_id"]) == (intent, None)
    claim = reopened.claim(intent["request_id"])
    assert claim["state"] == "PREPARING" and claim["revision"] == 2
    assert state.read_control() == claim
    complete = prepared_fields(intent)
    prepared = reopened.complete_preparation(intent["request_id"], claim, complete)
    assert prepared["status"] == "source_prepared"
    assert prepared["intent_hash"] == document_hash(intent)
    assert prepared["remote_published"] is prepared["ci_verified"] is prepared["deployed"] is False
    assert len(rows(db)) == 3
    assert state.history(intent["request_id"]) == (intent, prepared)
    assert state.read_control()["state"] == "SOURCE_PREPARED"
    before = rows(db)
    assert state.complete_preparation(intent["request_id"], claim, complete) == prepared
    assert rows(db) == before
    second.close()
    db.close()
    final, final_db = make_state(AdminTestDynamo(path))
    assert final.history(intent["request_id"]) == (intent, prepared)
    final_db.close()


def test_supported_baseline_reads_future_records_and_independently_refuses_every_write(monkeypatch):
    assert state_contracts.new_catalog_publication_state_enabled() is False
    state, db = make_state()
    inputs = fields()
    with monkeypatch.context() as enabled:
        enabled.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", True)
        enabled.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", True)
        intent = state.reserve(inputs, EMPTY_CONTROL_HASH)
        claim = state.claim(intent["request_id"])
        prepared = state.complete_preparation(intent["request_id"], claim, prepared_fields(intent))
    before = rows(db)
    assert state.history(intent["request_id"]) == (intent, prepared)
    assert state.read_control()["state"] == "SOURCE_PREPARED"
    for operation in (
        lambda: state.reserve(inputs, EMPTY_CONTROL_HASH),
        lambda: state.claim(intent["request_id"]),
        lambda: state.complete_preparation(intent["request_id"], claim, prepared_fields(intent)),
    ):
        with pytest.raises(PublicationUnavailable):
            operation()
    assert rows(db) == before


def test_request_replay_preserves_original_time_and_nested_plan_cannot_mutate_storage(writer):
    state, db = make_state()
    inputs = fields()
    saved = state.reserve(inputs, EMPTY_CONTROL_HASH)
    later = {**deepcopy(inputs), "created_at": NOW + 1}
    assert state.reserve(later, EMPTY_CONTROL_HASH) == saved
    assert db.transactions == 1
    saved["plan"]["forward"]["changes"]["title"]["after"] = "Injected saved object"
    inputs["target"]["rule_id"] = "pii.changed"
    original = state.read_request(later["request_id"])
    assert original["target"]["rule_id"] == "pii.email"
    assert original["plan"]["forward"]["changes"]["title"]["after"] == PROPOSAL["title"]


@pytest.mark.parametrize("change", [
    {"reason": "Another valid owner justification"},
    {"request_hash": "f" * 64}, {"base_source_sha": "f" * 40},
    {"operation": "reverse_latest"}, {"review_sha256": "f" * 64},
])
def test_same_action_reference_cannot_authorize_different_content(writer, change):
    state, db = make_state()
    inputs = fields()
    state.reserve(inputs, EMPTY_CONTROL_HASH)
    before = rows(db)
    with pytest.raises(PublicationConflict):
        state.reserve({**inputs, **change}, EMPTY_CONTROL_HASH)
    assert rows(db) == before and db.transactions == 1


def test_one_global_control_survives_key_owner_and_source_version_changes(writer):
    state, db = make_state()
    first = fields()
    intent = state.reserve(first, EMPTY_CONTROL_HASH)
    ident = state.row_id("control", CATALOG_ID)
    assert DynamoCatalogPublicationState.request_id(ACTOR, "publish-reference-123456") == intent["request_id"]
    assert DynamoCatalogPublicationState.request_id(OTHER, "publish-reference-123456") != intent["request_id"]
    other, _ = make_state(db)
    with pytest.raises(PublicationConflict):
        other.reserve(fields(actor=OTHER), EMPTY_CONTROL_HASH)
    with pytest.raises(PublicationConflict):
        other.reserve(fields(actor=OTHER), document_hash(state.read_control()))
    rotated, _ = make_state(db, key=b"rotated-publication-key-with-enough-entropy-0123456789")
    assert rotated.row_id("control", CATALOG_ID) == ident
    assert rotated.request_id(ACTOR, "publish-reference-123456") == intent["request_id"]
    with pytest.raises(PublicationUnavailable):
        rotated.read_control()
    with pytest.raises(PublicationUnavailable):
        rotated.reserve(fields(actor=OTHER), EMPTY_CONTROL_HASH)
    assert len(rows(db)) == 2 and db.transactions == 1


def test_two_independent_clients_cannot_strand_losing_request_or_acquire_twice(tmp_path, writer):
    path = tmp_path / "race.sqlite"
    barrier = Barrier(2)

    class CompetingTransport(AdminTestDynamo):
        def transact_write_items(self, **request):
            barrier.wait(timeout=5)
            return super().transact_write_items(**request)

    a, adb = make_state(CompetingTransport(path))
    b, bdb = make_state(CompetingTransport(path))
    requests = [fields(), fields(actor=OTHER)]

    def run(item):
        service, inputs = item
        try:
            return service.reserve(inputs, EMPTY_CONTROL_HASH)
        except PublicationConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [(a, requests[0]), (b, requests[1])]))
    assert sum(value is not None for value in results) == 1
    winner = next(value for value in results if value is not None)
    loser = next(request for request in requests if request["request_id"] != winner["request_id"])
    assert a.read_control()["request_id"] == winner["request_id"]
    assert b.history(loser["request_id"]) == (None, None)
    assert len(rows(adb)) == 2 and adb.transactions + bdb.transactions == 1


def test_lost_reserve_response_recovers_only_the_saved_intent(writer):
    state, db = make_state()
    db.lose_transaction_response = True
    intent = state.reserve(fields(), EMPTY_CONTROL_HASH)
    assert db.transactions == 1 and len(rows(db)) == 2
    assert state.read_control()["state"] == "REQUESTED"
    assert state.history(intent["request_id"]) == (intent, None)


def test_failed_reserve_is_atomic_and_never_creates_orphaned_control(writer):
    state, db = make_state()
    db.fail_audit = True
    with pytest.raises(PublicationUnavailable):
        state.reserve(fields(), EMPTY_CONTROL_HASH)
    assert rows(db) == [] and db.transactions == 0
    db.fail_audit = False
    assert state.reserve(fields(), EMPTY_CONTROL_HASH)["request_id"] == fields()["request_id"]


@pytest.mark.parametrize("reply", [None, [], {"Error": {"Code": "Uncertain"}},
    {"unexpected": "Uncertain"}, {"ResponseMetadata": None}, {"ResponseMetadata": {}},
    {"ResponseMetadata": {"HTTPStatusCode": 503}},
    {"ResponseMetadata": {"HTTPStatusCode": True}}])
def test_invalid_claim_acknowledgement_never_grants_worker_authority(writer, reply):
    class ClaimTransport(AdminTestDynamo):
        claim_reply = False

        def put_item(self, **request):
            result = super().put_item(**request)
            return deepcopy(reply) if self.claim_reply else result

    db = ClaimTransport()
    state, _ = make_state(db)
    intent = state.reserve(fields(), EMPTY_CONTROL_HASH)
    db.claim_reply = True
    with pytest.raises(PublicationUnavailable):
        state.claim(intent["request_id"])
    assert state.read_control()["state"] == "PREPARING"
    with pytest.raises(PublicationConflict):
        state.claim(intent["request_id"])
    assert state.history(intent["request_id"]) == (intent, None)


@pytest.mark.parametrize("reply", [None, [], {"Error": {"Code": "Uncertain"}},
    {"message": "Private provider failure"}, {"ResponseMetadata": {}},
    {"ResponseMetadata": {"HTTPStatusCode": 503}}, {"Item": None}])
def test_malformed_read_response_is_not_an_empty_catalog_control(writer, reply):
    class ReadTransport(AdminTestDynamo):
        def get_item(self, **_request):
            return deepcopy(reply)

    state, db = make_state(ReadTransport())
    with pytest.raises(PublicationUnavailable):
        state.read_control()
    with pytest.raises(PublicationUnavailable):
        state.reserve(fields(), EMPTY_CONTROL_HASH)
    assert rows(db) == []


def test_lost_claim_response_is_not_recreated_from_readback(writer):
    class ClaimTransport(AdminTestDynamo):
        lose_claim = False

        def put_item(self, **request):
            result = super().put_item(**request)
            if self.lose_claim:
                raise TimeoutError("Synthetic post-write response loss")
            return result

    db = ClaimTransport()
    state, _ = make_state(db)
    intent = state.reserve(fields(), EMPTY_CONTROL_HASH)
    db.lose_claim = True
    with pytest.raises(PublicationUnavailable):
        state.claim(intent["request_id"])
    db.lose_claim = False
    assert state.read_control()["state"] == "PREPARING"
    with pytest.raises(PublicationConflict):
        state.claim(intent["request_id"])
    with pytest.raises(PublicationConflict):
        state.reserve(fields(actor=OTHER), EMPTY_CONTROL_HASH)
    assert len(rows(db)) == 2


def test_racing_claim_full_document_condition_allows_one_acknowledged_winner(tmp_path, writer):
    path = tmp_path / "claims.sqlite"
    initial, initial_db = make_state(AdminTestDynamo(path))
    intent = initial.reserve(fields(), EMPTY_CONTROL_HASH)
    barrier = Barrier(2)

    class ClaimTransport(AdminTestDynamo):
        def put_item(self, **request):
            barrier.wait(timeout=5)
            return super().put_item(**request)

    contenders = [make_state(ClaimTransport(path))[0] for _ in range(2)]

    def claim(service):
        try:
            return service.claim(intent["request_id"])
        except PublicationUnavailable:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, contenders))
    assert sum(value is not None for value in results) == 1
    assert initial.read_control() == next(value for value in results if value is not None)
    assert len(rows(initial_db)) == 2


def test_lost_finish_response_has_readable_receipt_without_another_worker_claim(writer):
    state, db = make_state()
    intent = state.reserve(fields(), EMPTY_CONTROL_HASH)
    claim = state.claim(intent["request_id"])
    db.lose_transaction_response = True
    with pytest.raises(PublicationUnavailable):
        state.complete_preparation(intent["request_id"], claim, prepared_fields(intent))
    saved, complete = state.history(intent["request_id"])
    assert saved == intent and complete["candidate_source_sha"] == "d" * 40
    assert complete["deployed"] is False and len(rows(db)) == 3
    with pytest.raises(PublicationConflict):
        state.claim(intent["request_id"])
    before = db.transactions
    assert state.complete_preparation(intent["request_id"], claim, prepared_fields(intent)) == complete
    assert db.transactions == before


def test_failed_finish_preserves_claim_and_has_no_partial_prepared_receipt(writer):
    state, db = make_state()
    intent = state.reserve(fields(), EMPTY_CONTROL_HASH)
    claim = state.claim(intent["request_id"])
    db.fail_audit = True
    with pytest.raises(PublicationUnavailable):
        state.complete_preparation(intent["request_id"], claim, prepared_fields(intent))
    assert state.read_control() == claim
    assert state.history(intent["request_id"]) == (intent, None)
    assert len(rows(db)) == 2


@pytest.mark.parametrize("change", [
    {"base_source_sha": "e" * 40}, {"candidate_source_sha": "c" * 40},
    {"candidate_source_sha": "short"}, {"history_sha256": "bad"},
    {"candidate_metadata_sha256": "f" * 64}, {"candidate_registry_version": "5"},
    {"recorded_at": NOW - 1}, {"recorded_at": True}, {"remote_published": True},
])
def test_preparation_receipt_cannot_claim_wrong_source_or_stage(writer, change):
    state, db = make_state()
    intent = state.reserve(fields(), EMPTY_CONTROL_HASH)
    claim = state.claim(intent["request_id"])
    before = rows(db)
    with pytest.raises(PublicationUnavailable):
        state.complete_preparation(intent["request_id"], claim, prepared_fields(intent, **change))
    assert rows(db) == before


def test_finished_global_control_never_expires_or_reopens_for_another_publication(writer):
    state, db = make_state()
    intent = state.reserve(fields(), EMPTY_CONTROL_HASH)
    claim = state.claim(intent["request_id"])
    state.complete_preparation(intent["request_id"], claim, prepared_fields(intent))
    before = rows(db)
    next_inputs = fields(actor=OTHER)
    next_inputs["created_at"] = 253402300799
    with pytest.raises(PublicationConflict):
        state.reserve(next_inputs, document_hash(state.read_control()))
    assert rows(db) == before


@pytest.mark.parametrize("mutation", [
    lambda x: x.update(version=True), lambda x: x.update(version=2),
    lambda x: x.update(contract="stripe-period-end-cancellation-v1"),
    lambda x: x.update(kind="unknown"), lambda x: x.update(extra="injected"),
    lambda x: x.update(catalog_id="f" * 64), lambda x: x.update(actor_sub="owner"),
    lambda x: x.update(base_source_sha="A" * 40), lambda x: x.update(operation="deploy"),
    lambda x: x.update(created_at=False), lambda x: x.update(created_at=0),
    lambda x: x.update(reason="Short"), lambda x: x.update(reason="Unseen\u202e source authority"),
    lambda x: x.update(reason=" Leading whitespace is refused"),
    lambda x: x.update(candidate_registry_version="03"),
    lambda x: x.update(candidate_registry_version="5"),
    lambda x: x.update(candidate_metadata_sha256=x["baseline_sha256"]),
    lambda x: x["target"].update(extra="injected"),
    lambda x: x["plan"].update(published=True),
    lambda x: x["plan"].update(acceptance_tests_run=True),
    lambda x: x["plan"].update(extra="unreviewed"),
    lambda x: x["plan"].update(changed_fields=["title", "title"]),
    lambda x: x["plan"]["rollback"]["changes"]["title"].update(after="Different original"),
    lambda x: x["plan"]["forward"].update(candidate_registry_version="5"),
    lambda x: x["plan"].update(reviewed_proposal_sha256="f" * 64),
])
def test_even_resigned_structural_corruption_fails_before_persistence(writer, mutation):
    state, db = make_state()
    intent = state.reserve(fields(), EMPTY_CONTROL_HASH)
    bad = deepcopy(intent)
    mutation(bad)
    # A compromised caller cannot rely on signing to bypass schema/link checks.
    with pytest.raises(PublicationUnavailable):
        state._seal(bad)
    assert len(rows(db)) == 2


@pytest.mark.parametrize("raw_mutation", [
    lambda raw: raw.replace('"version":1', '"version":1,"version":1'),
    lambda raw: " " + raw,
    lambda raw: raw.replace('"reason":', '"\\u0072eason":'),
    lambda raw: raw.replace("Clarify", "Changed", 1),
    lambda raw: raw + " " * MAX_DOCUMENT_BYTES,
    lambda raw: raw.replace('"created_at":1795500000', '"created_at":NaN'),
])
def test_persisted_ambiguous_noncanonical_tampered_or_oversized_documents_are_refused(writer, raw_mutation):
    state, db = make_state()
    intent = state.reserve(fields(), EMPTY_CONTROL_HASH)
    stored = record(db, "intent", intent["request_id"])
    stored[state.ATTRIBUTE]["S"] = raw_mutation(stored[state.ATTRIBUTE]["S"])
    db.seed(TABLE, stored)
    with pytest.raises(PublicationUnavailable):
        state.history(intent["request_id"])


@pytest.mark.parametrize("missing", ["control", "intent"])
def test_orphaned_control_or_intent_does_not_authorize_history_or_claim(writer, missing):
    state, db = make_state()
    intent = state.reserve(fields(), EMPTY_CONTROL_HASH)
    identifier = CATALOG_ID if missing == "control" else intent["request_id"]
    db.connection.execute("DELETE FROM items WHERE table_name=? AND item_key=?",
        (TABLE, state.row_id(missing, identifier)))
    with pytest.raises(PublicationUnavailable):
        state.history(intent["request_id"])
    with pytest.raises(PublicationUnavailable):
        state.claim(intent["request_id"])


def test_missing_or_foreign_prepared_receipt_is_not_accepted(writer):
    state, db = make_state()
    intent = state.reserve(fields(), EMPTY_CONTROL_HASH)
    claim = state.claim(intent["request_id"])
    state.complete_preparation(intent["request_id"], claim, prepared_fields(intent))
    saved = record(db, "prepared", intent["request_id"])
    body = json.loads(saved[state.ATTRIBUTE]["S"])
    body["intent_hash"] = "f" * 64
    saved[state.ATTRIBUTE]["S"] = json.dumps(state._seal(body), sort_keys=True, separators=(",", ":"))
    db.seed(TABLE, saved)
    with pytest.raises(PublicationUnavailable):
        state.history(intent["request_id"])
    with pytest.raises(PublicationUnavailable):
        state.read_control()


@pytest.mark.parametrize("actor,reference", [
    ("owner", "valid-reference-123456"), (ACTOR, "short"),
    (ACTOR, "includes spaces in reference"), (ACTOR, "a" * 97),
    (None, "valid-reference-123456"), (ACTOR, None),
])
def test_invalid_stable_request_identity_is_refused(actor, reference):
    with pytest.raises(PublicationUnavailable):
        DynamoCatalogPublicationState.request_id(actor, reference)


@pytest.mark.parametrize("kind,identifier", [
    ([], "a" * 64), ("control", "a" * 64), ("bad", "a" * 64), ("intent", "short"),
])
def test_invalid_state_row_identity_is_refused(kind, identifier):
    with pytest.raises(PublicationUnavailable):
        DynamoCatalogPublicationState.row_id(kind, identifier)


def test_valid_unicode_metadata_keeps_the_original_proposal_fingerprint(writer):
    proposal = {**PROPOSAL, "title": "Reviewed café email rule"}
    plan = export_plan(SOURCE, proposal)
    state, _ = make_state()
    intent = state.reserve(fields(plan=plan), EMPTY_CONTROL_HASH)
    assert state.read_request(intent["request_id"])["plan"]["changed_metadata"]["title"]["after"] == proposal["title"]
