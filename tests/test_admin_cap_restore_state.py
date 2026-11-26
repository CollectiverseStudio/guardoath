"""Durable local-cap restoration at the real tenant/audit transaction boundary."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from threading import Barrier

import pytest

from admin_test_support import AdminTestDynamo
from guardoath import state_contracts
from guardoath import admin_cap_restore_state as state_module
from guardoath.admin_audit import AdminAuditReader, AdminAuditUnavailable
from guardoath.admin_audit_integrity import seal_audit
from guardoath.admin_cap_restore_state import (
    FIELDS, CapRestoreStateConflict, CapRestoreStateUnavailable, DynamoCapRestoreState,
)
from guardoath.dynamodb_store import DynamoDBTenantStore, record_json, to_item
from guardoath.tenant_record import TenantRecord

KEY = b"synthetic-cap-restoration-audit-key-0123456789"
ACTOR = "12345678-1234-1234-1234-123456789012"
TENANT = "usr-cap-restore"
NOW = "2026-11-26T12:00:00Z"
REFERENCE = "restore-request-123456789"


def tenant(**changes):
    return TenantRecord.model_validate({"tenant_id": TENANT, "display_name": "Synthetic capped account",
        "revision": 7, "demo": False, "status": "disabled", "billing_mode": "stripe",
        "payer_id": "cus_restore", "stripe_subscription_id": "sub_restore", "subscription_status": "active",
        "monthly_allowance": 4000, "admin_monthly_allowance_cap": 4000,
        "stripe_plan_monthly_allowance": 10000, "overage": "block",
        "key_hashes": ["a" * 64], "usage": {"2026-11": 789}, "input_check_pii": "block",
        "input_rule_actions": {"pii.email_address": "off"},
        "output_rule_actions": {"pii.email_address": "block"}, "input_attachment_csv_mode": "off", **changes})


def prepared(state, record=None, *, actor=ACTOR, reference=REFERENCE):
    record = record or tenant()
    original = record_json(record)
    preview = "cr1.synthetic-reviewed-reference"
    confirmation = f"RESTORE {record.tenant_id} TO {record.stripe_plan_monthly_allowance} REQUESTS PER MONTH"
    reason = "Remove the reviewed saved local request cap"
    fields = {"event_id": state.action_id(actor, reference), "tenant_id": record.tenant_id,
        "actor_sub": actor, "operation": "tenant.allowance_restore", "when_utc": NOW, "reason": reason,
        "prior_revision": record.revision, "new_revision": record.revision + 1,
        "old_status": record.status, "new_status": record.status,
        "old_subscription_status": "active", "new_subscription_status": "active", "provider_status": "active",
        "old_limit": record.monthly_allowance, "new_limit": record.stripe_plan_monthly_allowance,
        "plan_limit": record.stripe_plan_monthly_allowance, "cap_limit": record.admin_monthly_allowance_cap,
        "target_fingerprint": hashlib.sha256(b"synthetic-private-review-target").hexdigest(),
        "request_fingerprint": state.request_fingerprint(actor=actor, tenant_id=record.tenant_id,
            expected_revision=record.revision, preview_reference=preview, confirmation=confirmation, reason=reason)}
    updated = {**json.loads(original), "monthly_allowance": fields["new_limit"], "updated_at": NOW}
    updated.pop("admin_monthly_allowance_cap")
    return fields, {"tenant_table": "tenant_table", "original_record": original,
                    "updated_record": json.dumps(updated, sort_keys=True, separators=(",", ":"))}


def make(db):
    db.seed("tenant_table", to_item(tenant()))
    return DynamoCapRestoreState(db, "audit_table", KEY), db


@pytest.fixture
def writer(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", True)


@pytest.fixture
def state_db(tmp_path, writer):
    state, db = make(AdminTestDynamo(tmp_path / "cap-restore.sqlite3"))
    yield state, db
    db.close()


@pytest.mark.parametrize("cap", [0, 4000])
@pytest.mark.parametrize("status", ["active", "disabled"])
def test_restoration_changes_only_saved_cap_allowance_revision_and_timestamp(state_db, cap, status):
    state, db = state_db
    before = tenant(monthly_allowance=cap, admin_monthly_allowance_cap=cap, status=status)
    db.seed("tenant_table", to_item(before))
    other = tenant(tenant_id="usr-other", payer_id="cus_other", stripe_subscription_id="sub_other")
    db.seed("tenant_table", to_item(other))
    fields, request = prepared(state, before)
    result = state.commit(fields, **request)
    after = DynamoDBTenantStore("tenant_table", db).get(TENANT)
    expected = before.model_dump(mode="json")
    expected.update(monthly_allowance=10000, admin_monthly_allowance_cap=None, revision=8, updated_at=NOW)
    assert after.model_dump(mode="json") == expected
    assert DynamoDBTenantStore("tenant_table", db).get("usr-other") == other
    assert db.transactions == 1 and result == {**fields, "status": "applied",
        "integrity_status": "signed_verified", "replayed": False}
    assert state.client is db and state.table_name == "audit_table" and state.audit_key == state.key == KEY
    saved = db.audit_items()
    assert len(saved) == 1 and set(saved[0]) == {*FIELDS, "integrity_mac"}
    assert all(private not in str(saved) for private in ("cus_restore", "sub_restore", REFERENCE, "cr1."))
    assert "admin_monthly_allowance_cap" not in json.loads(db.get_item(
        TableName="tenant_table", Key={"tenant_id": {"S": TENANT}})["Item"]["record"]["S"])
    audit = AdminAuditReader(db, "audit_table", verify_key=KEY).read(fields["event_id"])
    assert audit["operation"] == "tenant.allowance_restore" and audit["integrity_status"] == "signed_verified"
    assert audit["change"] == {"field": "monthly_allowance", "before": cap, "after": 10000,
        "plan_allowance": 10000, "operator_cap_before": cap, "operator_cap_after": None,
        "cap_removed": True, "tenant_status": status, "subscription_status": "active",
        "provider_status": "active", "target_fingerprint": fields["target_fingerprint"]}


def test_reopened_history_and_exact_replay_survive_disabled_writer_and_changed_tenant(tmp_path, monkeypatch, writer):
    path = tmp_path / "durable.sqlite3"
    state, db = make(AdminTestDynamo(path))
    fields, request = prepared(state)
    original = state.commit(fields, **request)
    db.seed("tenant_table", to_item(tenant(revision=9, monthly_allowance=1000, admin_monthly_allowance_cap=1000)))
    db.close()
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", False)
    reopened = AdminTestDynamo(path)
    try:
        reader = DynamoCapRestoreState(reopened, "audit_table", KEY)
        expected = {**original, "replayed": True}
        assert reader.history(fields["event_id"]) == expected
        # Completed requests recover before new document admission.
        assert reader.commit(fields, **{**request, "original_record": "unavailable", "updated_record": "unavailable"},
            before_write=lambda: pytest.fail("Historical receipts must bypass the current write guard")) == expected
        assert reopened.transactions == 0
        saved = DynamoDBTenantStore("tenant_table", reopened).get(TENANT)
        assert saved.revision == 9 and saved.admin_monthly_allowance_cap == 1000
        monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", "invalid")
        assert reader.history(fields["event_id"]) == expected
    finally:
        reopened.close()


def test_writer_gate_refuses_new_state_before_transaction_but_allows_missing_history(state_db, monkeypatch):
    state, db = state_db
    fields, request = prepared(state)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", False)
    with pytest.raises(CapRestoreStateUnavailable, match="compatibility"):
        state.commit(fields, **request)
    assert state.history(fields["event_id"]) is None and db.transactions == 0 and not db.audit_items()


def test_action_identity_is_owner_bound_and_request_fingerprint_binds_every_review_input(state_db):
    state, _ = state_db
    assert state.action_id(ACTOR, REFERENCE) == state.action_id(ACTOR, REFERENCE)
    assert state.action_id(ACTOR, REFERENCE) != state.action_id("other-owner-123456789", REFERENCE)
    assert state.action_id(ACTOR, REFERENCE) != state.action_id(ACTOR, REFERENCE + "-new")
    original = dict(actor=ACTOR, tenant_id=TENANT, expected_revision=7, preview_reference="cr1.reference",
                    confirmation=f"RESTORE {TENANT} TO 10000 REQUESTS PER MONTH", reason="Restore reviewed request cap")
    changes = {"actor": "other-owner-123456789", "tenant_id": "usr-other", "expected_revision": 8,
               "preview_reference": "cr1.changed", "confirmation": "RESTORE usr-other TO 10000 REQUESTS PER MONTH",
               "reason": "Restore a different reviewed cap"}
    fingerprint = state.request_fingerprint(**original)
    assert all(state.request_fingerprint(**{**original, key: value}) != fingerprint for key, value in changes.items())


def test_used_reference_conflicts_on_changed_request_or_semantic_receipt(state_db):
    state, db = state_db
    fields, request = prepared(state)
    state.commit(fields, **request)
    with pytest.raises(CapRestoreStateConflict):
        state.history(fields["event_id"], request_fingerprint="b" * 64)
    with pytest.raises(CapRestoreStateConflict):
        state.commit({**fields, "request_fingerprint": "b" * 64}, **request)
    with pytest.raises(CapRestoreStateConflict):
        state.commit({**fields, "tenant_id": "usr-other"}, **request)
    assert state.commit({**fields, "when_utc": "2026-11-27T12:00:00Z"}, **request)["when_utc"] == NOW
    assert db.transactions == 1


@pytest.mark.parametrize("field,value", [
    ("status", "active"), ("payer_id", "cus_other"), ("stripe_subscription_id", "sub_other"),
    ("stripe_plan_monthly_allowance", 20000), ("subscription_status", "inactive"),
    ("monthly_allowance", 9000), ("overage", "allow_and_record"), ("key_hashes", []),
    ("input_rule_actions", {"pii.email_address": "block"}), ("output_rule_actions", {}),
    ("admin_monthly_allowance_cap", None), ("admin_monthly_allowance_cap", 0),
    ("usage", {}), ("revision", 8), ("timeout_ms", 1000.0), ("demo", True),
    ("tenant_id", "usr-other"), ("updated_at", "2026-11-27T12:00:00Z"),
])
def test_other_document_changes_cannot_be_smuggled_into_restoration(state_db, field, value):
    state, db = state_db
    fields, request = prepared(state)
    changed = {**json.loads(request["updated_record"]), field: value}
    with pytest.raises((CapRestoreStateConflict, ValueError)):
        state.commit(fields, **{**request, "updated_record": json.dumps(changed)})
    assert db.transactions == 0 and not db.audit_items()
    assert DynamoDBTenantStore("tenant_table", db).get(TENANT) == tenant()


@pytest.mark.parametrize("field,value", [
    ("demo", True), ("billing_mode", "internal"), ("subscription_status", "past_due"),
    ("admin_monthly_allowance_cap", None), ("admin_monthly_allowance_cap", True),
    ("stripe_plan_monthly_allowance", 20000), ("stripe_plan_monthly_allowance", None),
    ("monthly_allowance", 3000), ("overage", "allow_and_record"),
    ("payer_id", None), ("stripe_subscription_id", None), ("tenant_id", "usr-other"),
])
def test_unproven_or_incoherent_saved_cap_is_not_restorable(state_db, field, value):
    state, db = state_db
    fields, request = prepared(state)
    before = {**json.loads(request["original_record"]), field: value}
    after = {**before, "monthly_allowance": 10000, "updated_at": NOW}
    after.pop("admin_monthly_allowance_cap")
    with pytest.raises((CapRestoreStateConflict, ValueError)):
        state.commit(fields, **{**request, "original_record": json.dumps(before), "updated_record": json.dumps(after)})
    assert db.transactions == 0 and not db.audit_items()


@pytest.mark.parametrize("changed", ["revision", "document", "missing"])
def test_exact_authoritative_revision_and_document_condition_blocks_races(state_db, changed, monkeypatch):
    state, db = state_db
    fields, request = prepared(state)
    if changed == "revision":
        db.seed("tenant_table", to_item(tenant(revision=8)))
    elif changed == "document":
        db.seed("tenant_table", to_item(tenant(input_rule_actions={"pii.email_address": "block"})))
    else:
        db.connection.execute("DELETE FROM items WHERE table_name='tenant_table'")
    attempts = []
    actual = db.transact_write_items
    def send(**kwargs):
        attempts.append(kwargs)
        return actual(**kwargs)
    monkeypatch.setattr(db, "transact_write_items", send)
    with pytest.raises(CapRestoreStateConflict):
        state.commit(fields, **request)
    assert len(attempts) == 1 and db.transactions == 0 and not db.audit_items()


def test_competing_reopened_clients_cannot_both_restore_same_revision(tmp_path, writer):
    path = tmp_path / "race.sqlite3"
    first, first_db = make(AdminTestDynamo(path))
    second_db = AdminTestDynamo(path)
    second = DynamoCapRestoreState(second_db, "audit_table", KEY)
    barrier = Barrier(2)
    def apply(state, reference):
        fields, request = prepared(state, reference=reference)
        barrier.wait(timeout=5)
        try:
            return state.commit(fields, **request)
        except CapRestoreStateConflict:
            return "conflict"
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(apply, state, REFERENCE + str(index)) for index, state in enumerate((first, second))]
            results = [future.result(timeout=10) for future in futures]
        assert sum(isinstance(result, dict) for result in results) == 1 and results.count("conflict") == 1
        assert first_db.transactions + second_db.transactions == 1 and len(first_db.audit_items()) == 1
        assert DynamoDBTenantStore("tenant_table", second_db).get(TENANT).revision == 8
    finally:
        first_db.close()
        second_db.close()


@pytest.mark.parametrize("failure", ["audit", "transaction"])
def test_failed_atomic_write_never_leaves_a_partial_tenant_change_or_receipt(state_db, failure):
    state, db = state_db
    fields, request = prepared(state)
    setattr(db, "fail_" + failure, True)
    with pytest.raises(CapRestoreStateUnavailable):
        state.commit(fields, **request)
    assert db.transactions == 0 and not db.audit_items()
    assert DynamoDBTenantStore("tenant_table", db).get(TENANT) == tenant()


def test_lost_commit_response_is_resolved_by_one_read_and_never_a_second_write(state_db, monkeypatch):
    state, db = state_db
    fields, request = prepared(state)
    calls = {"reads": 0, "writes": 0}
    original_get, original_write = db.get_item, db.transact_write_items
    def get(**kwargs):
        calls["reads"] += 1
        assert kwargs["ConsistentRead"] is True
        return original_get(**kwargs)
    def write(**kwargs):
        calls["writes"] += 1
        return original_write(**kwargs)
    monkeypatch.setattr(db, "get_item", get)
    monkeypatch.setattr(db, "transact_write_items", write)
    db.lose_transaction_response = True
    assert state.commit(fields, **request)["replayed"] is True
    assert calls == {"reads": 2, "writes": 1} and db.transactions == 1


def test_missing_receipt_after_timeout_can_later_resolve_without_redispatch(state_db, monkeypatch):
    state, db = state_db
    fields, request = prepared(state)
    delayed = []
    actual_write = db.transact_write_items
    def timeout(**kwargs):
        delayed.append(kwargs)
        raise TimeoutError("Synthetic pending transaction")
    monkeypatch.setattr(db, "transact_write_items", timeout)
    with pytest.raises(CapRestoreStateUnavailable, match="retain"):
        state.commit(fields, **request)
    assert state.history(fields["event_id"]) is None and len(delayed) == 1
    # Simulate the original outstanding transaction completing at the boundary.
    actual_write(**delayed[0])
    assert state.history(fields["event_id"])["status"] == "applied"
    assert len(delayed) == 1 and db.transactions == 1


@pytest.mark.parametrize("expired", [False, True])
def test_final_trusted_guard_runs_after_slow_history_and_sealing_before_any_transaction(state_db, monkeypatch, expired):
    state, db = state_db
    fields, request = prepared(state)
    order, elapsed = [], [0]
    original_get, original_seal, original_write = db.get_item, state_module.seal_audit, db.transact_write_items
    class ExpiredReview(Exception):
        pass
    def get(**kwargs):
        order.append("history")
        elapsed[0] = 5 if expired else 1
        return original_get(**kwargs)
    def seal(*args):
        order.append("sealed")
        return original_seal(*args)
    def guard():
        order.append("guard")
        if elapsed[0] >= 3:
            raise ExpiredReview("Synthetic reviewed period expired")
    def write(**kwargs):
        order.append("transaction")
        return original_write(**kwargs)
    monkeypatch.setattr(db, "get_item", get)
    monkeypatch.setattr(state_module, "seal_audit", seal)
    monkeypatch.setattr(db, "transact_write_items", write)
    if expired:
        with pytest.raises(ExpiredReview):
            state.commit(fields, **request, before_write=guard)
        assert order == ["history", "sealed", "guard"] and db.transactions == 0 and not db.audit_items()
    else:
        assert state.commit(fields, **request, before_write=guard)["status"] == "applied"
        assert order == ["history", "sealed", "guard", "transaction"] and db.transactions == 1


def test_uncertain_write_plus_failed_recovery_read_remains_unavailable(state_db, monkeypatch):
    state, db = state_db
    fields, request = prepared(state)
    original_get = db.get_item
    reads = []
    def get(**kwargs):
        reads.append(kwargs)
        if len(reads) == 2:
            raise TimeoutError("Synthetic recovery read outage")
        return original_get(**kwargs)
    monkeypatch.setattr(db, "get_item", get)
    db.lose_transaction_response = True
    with pytest.raises(CapRestoreStateUnavailable):
        state.commit(fields, **request)
    assert len(reads) == 2 and db.transactions == 1
    assert state.history(fields["event_id"])["status"] == "applied"


@pytest.mark.parametrize("name", ["event_id", "actor_sub", "tenant_id", "reason", "new_limit", "target_fingerprint"])
def test_signed_tampering_fails_both_history_readers(state_db, name):
    state, db = state_db
    fields, request = prepared(state)
    state.commit(fields, **request)
    item = db.audit_items()[0]
    tag = next(iter(item[name]))
    item[name][tag] += "1"
    # Preserve the physical lookup key for an identity-tampering fixture.
    db.connection.execute("UPDATE items SET body=? WHERE table_name='audit_table'", (json.dumps(item),))
    with pytest.raises(CapRestoreStateUnavailable):
        state.history(fields["event_id"])
    with pytest.raises(AdminAuditUnavailable):
        AdminAuditReader(db, "audit_table", verify_key=KEY).read(fields["event_id"])


@pytest.mark.parametrize("name,value", [
    ("new_limit", {"N": "09999"}), ("new_limit", {"N": "10000.0"}), ("cap_limit", {"N": "10000"}),
    ("prior_revision", {"N": "9007199254740991"}), ("new_revision", {"N": "9"}),
    ("old_subscription_status", {"S": "inactive"}), ("provider_status", {"S": "trialing"}),
    ("new_status", {"S": "active"}), ("when_utc", {"S": "2026-02-30T12:00:00Z"}),
    ("target_fingerprint", {"S": "invalid"}), ("request_fingerprint", {"S": "F" * 64}),
    ("actor_sub", {"S": ACTOR + "\n"}), ("reason", {"S": "short"}),
    ("old_limit", {"S": "4000"}), ("old_limit", {"N": "4000", "S": "4000"}),
])
def test_valid_signature_cannot_hide_invalid_restoration_semantics(state_db, name, value):
    state, db = state_db
    fields, request = prepared(state)
    state.commit(fields, **request)
    item = {key: val for key, val in db.audit_items()[0].items() if key != "integrity_mac"}
    item[name] = value
    if len(value) != 1:
        with pytest.raises(ValueError):
            seal_audit(item, KEY)
        return
    db.seed("audit_table", seal_audit(item, KEY))
    with pytest.raises(CapRestoreStateUnavailable):
        state.history(fields["event_id"])
    with pytest.raises(AdminAuditUnavailable):
        AdminAuditReader(db, "audit_table", verify_key=KEY).read(fields["event_id"])


@pytest.mark.parametrize("missing", ["integrity_mac", "cap_limit", "request_fingerprint", "target_fingerprint", "provider_status"])
def test_missing_required_receipt_evidence_is_never_interpreted_as_absence(state_db, missing):
    state, db = state_db
    fields, request = prepared(state)
    state.commit(fields, **request)
    item = db.audit_items()[0]
    item.pop(missing)
    if missing != "integrity_mac":
        item.pop("integrity_mac")
        item = seal_audit(item, KEY)
    db.seed("audit_table", item)
    with pytest.raises(CapRestoreStateUnavailable):
        state.history(fields["event_id"])


@pytest.mark.parametrize("response", [None, [], {"Item": None}, {"Item": []}])
def test_malformed_history_transport_is_not_a_not_found_receipt(state_db, monkeypatch, response):
    state, db = state_db
    fields, _ = prepared(state)
    monkeypatch.setattr(db, "get_item", lambda **_: deepcopy(response))
    with pytest.raises(CapRestoreStateUnavailable):
        state.history(fields["event_id"])


def test_unexpected_receipt_fields_and_wrong_signing_key_fail_closed(state_db):
    state, db = state_db
    fields, request = prepared(state)
    state.commit(fields, **request)
    other = DynamoCapRestoreState(db, "audit_table", b"different-independent-audit-key-0123456789")
    with pytest.raises(CapRestoreStateUnavailable):
        other.history(fields["event_id"])
    item = db.audit_items()[0]
    item["unsigned_provider_id"] = {"S": "sub_injected"}
    db.seed("audit_table", item)
    with pytest.raises(CapRestoreStateUnavailable):
        state.history(fields["event_id"])


def test_duplicate_json_fields_and_unsigned_payload_extras_cannot_reach_transaction(state_db):
    state, db = state_db
    fields, request = prepared(state)
    duplicate = request["original_record"][:-1] + ',"monthly_allowance":4000}'
    with pytest.raises(CapRestoreStateUnavailable):
        state.commit(fields, **{**request, "original_record": duplicate})
    with pytest.raises(ValueError):
        state.commit({**fields, "provider_id": "sub_injected"}, **request)
    assert db.transactions == 0 and not db.audit_items()
