"""Reader-first contract for restoring a saved local request cap."""
import importlib.util
import itertools
from pathlib import Path

import pytest

from admin_test_support import AdminTestDynamo
from guardoath import state_contracts
from guardoath.admin_audit import AdminAuditReader
from guardoath.admin_cap_restore_state import CONTRACT, CapRestoreStateUnavailable, DynamoCapRestoreState
from guardoath.dynamodb_store import DynamoDBTenantStore
from guardoath.tenant_store import MemoryTenantStore
from test_admin_cap_restore_state import ACTOR, KEY, TENANT, make, prepared
from test_rollback_compatibility import OLD, SHA, archive, gate, metadata, verify

PRIOR_CONTRACTS = (
    "subscription-cap-provenance-v1", "stripe-period-end-cancellation-v1",
    "catalog-publication-v1", "catalog-publication-lifecycle-v1", "stripe-invoice-refund-v1",
)
FLAGS = (
    "ENABLE_NEW_SUBSCRIPTION_STATE", "ENABLE_NEW_CANCELLATION_STATE",
    "ENABLE_NEW_CATALOG_PUBLICATION_STATE", "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE",
    "ENABLE_NEW_REFUND_STATE", "ENABLE_NEW_CAP_RESTORATION_STATE",
)


def test_source_reader_adds_support_without_enabling_new_writes_through_environment(monkeypatch):
    for name in ("ENABLE_NEW_CAP_RESTORATION_STATE", "GUARDOATH_ENABLE_NEW_CAP_RESTORATION_STATE",
                 "ENABLE_NEW_CAP_RESTORE_STATE", "GUARDOATH_CAP_RESTORATION_WRITER_ENABLED",
                 "GUARDOATH_ADMIN_TEST_WRITER_PHASE"):
        monkeypatch.setenv(name, "1")
    assert state_contracts.ENABLE_NEW_CAP_RESTORATION_STATE is False
    assert state_contracts.new_cap_restoration_state_enabled() is False
    assert all(getattr(state_contracts, name) is True for name in FLAGS[:-1])
    declaration = state_contracts.release_metadata(SHA)
    assert declaration["supported_state_contracts"] == [*PRIOR_CONTRACTS, CONTRACT]
    assert declaration["introduced_state_contracts"] == list(PRIOR_CONTRACTS)


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", [], {}])
def test_restoration_gate_requires_an_actual_source_boolean(monkeypatch, invalid):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", invalid)
    with pytest.raises(ValueError):
        state_contracts.new_cap_restoration_state_enabled()
    with pytest.raises(ValueError):
        state_contracts.release_metadata(SHA)


@pytest.mark.parametrize("enabled", list(itertools.product([False, True], repeat=6)))
def test_six_contract_writer_phases_are_independent(monkeypatch, enabled):
    for name, value in zip(FLAGS, enabled):
        monkeypatch.setattr(state_contracts, name, value)
    contracts = (*PRIOR_CONTRACTS, CONTRACT)
    declaration = state_contracts.release_metadata(SHA)
    assert declaration["supported_state_contracts"] == list(contracts)
    assert declaration["introduced_state_contracts"] == [
        contract for contract, value in zip(contracts, enabled) if value]


def test_actual_reader_artifact_precedes_future_writer_and_requires_compatible_digest_bound_backup(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("cap_restoration_build", root / "deploy/aws/build_lambda.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    actual = build.release_metadata(SHA)
    assert actual == state_contracts.release_metadata(SHA)
    assert actual["supported_state_contracts"] == [*PRIOR_CONTRACTS, CONTRACT]
    assert actual["introduced_state_contracts"] == list(PRIOR_CONTRACTS)
    previous = archive(tmp_path / "previous-five-contracts.zip",
        metadata(OLD, support=PRIOR_CONTRACTS, writes=PRIOR_CONTRACTS))
    reader = archive(tmp_path / "restoration-reader.zip", actual)
    assert verify(reader, previous)["status"] == "compatible"
    # Synthetic future source activation is not evidence of an accepted reader
    # deployment. The packager continues reading the unchanged source gate.
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", True)
    future = state_contracts.release_metadata(SHA)
    assert future["introduced_state_contracts"] == [*PRIOR_CONTRACTS, CONTRACT]
    assert build.release_metadata(SHA) == actual
    writer = archive(tmp_path / "future-restoration-writer.zip", future)
    with pytest.raises(ValueError, match="compatibility baseline"):
        verify(writer, previous)
    compatible = archive(tmp_path / "synthetic-reader-backup.zip", {**actual, "source_sha": OLD})
    result = verify(writer, compatible)
    assert result["status"] == "compatible"
    assert result["rollback_code_sha256"] == gate.archive_digest(compatible)
    assert result["state_contracts"] == sorted((*PRIOR_CONTRACTS, CONTRACT))
    dropped = archive(tmp_path / "reader-support-dropped.zip",
        metadata(SHA, support=PRIOR_CONTRACTS, writes=PRIOR_CONTRACTS))
    with pytest.raises(ValueError, match="drops deployed"):
        verify(dropped, compatible)
    accepted_digest = gate.archive_digest(compatible)
    archive(compatible, {**actual, "source_sha": "c" * 40})
    with pytest.raises(ValueError, match="CodeSha256"):
        gate.verify_rollback(writer, compatible, expected_sha=SHA, backup_code_sha256=accepted_digest)


def test_signed_restoration_reopens_in_disabled_reader_and_cannot_emit_new_state(tmp_path, monkeypatch):
    path = tmp_path / "reader-recovery.sqlite3"
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", True)
    state, db = make(AdminTestDynamo(path))
    fields, request = prepared(state)
    receipt = state.commit(fields, **request)
    before_rows = db.audit_items()
    before_tenant = DynamoDBTenantStore("tenant_table", db).get(TENANT)
    db.close()
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", False)
    reader_db = AdminTestDynamo(path)
    try:
        reader = DynamoCapRestoreState(reader_db, "audit_table", KEY)
        assert reader.history(fields["event_id"]) == {**receipt, "replayed": True}
        generic = AdminAuditReader(reader_db, "audit_table", verify_key=KEY).read(fields["event_id"])
        assert generic["integrity_status"] == "signed_verified" and generic["change"]["cap_removed"] is True
        fresh = {**fields, "event_id": reader.action_id(ACTOR, "different-request-123456789")}
        with pytest.raises(CapRestoreStateUnavailable, match="compatibility"):
            reader.commit(fresh, **request)
        assert reader_db.transactions == 0 and reader_db.audit_items() == before_rows
        assert DynamoDBTenantStore("tenant_table", reader_db).get(TENANT) == before_tenant
    finally:
        reader_db.close()


@pytest.mark.parametrize("kind", ["memory", "dynamo"])
def test_restored_provenance_survives_ordinary_reconciliation_and_delinquency_recovery(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", True)
    state, db = make(AdminTestDynamo(tmp_path / "reconcile.sqlite3"))
    try:
        fields, request = prepared(state)
        state.commit(fields, **request)
        monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", False)
        saved = DynamoDBTenantStore("tenant_table", db).get(TENANT)
        store = DynamoDBTenantStore("tenant_table", db) if kind == "dynamo" else MemoryTenantStore((saved,))
        for provider_status, expected_limit in (("active", 10000), ("past_due", 0), ("active", 10000)):
            current = store.get(TENANT)
            updated = store.apply_verified_subscription(TENANT, customer_id="cus_restore",
                subscription_id="sub_restore", expected_revision=current.revision, plan_monthly_allowance=10000,
                provider_subscription={"id": "sub_restore", "object": "subscription", "customer": "cus_restore",
                    "status": provider_status, "collection_method": "charge_automatically", "pause_collection": None,
                    "items": {"has_more": False, "data": [{"quantity": 1, "price": {"id": "price_restore"}}]}})
            assert updated.monthly_allowance == expected_limit
            assert updated.admin_monthly_allowance_cap is None and updated.stripe_plan_monthly_allowance == 10000
            assert updated.status == "disabled" and updated.overage == "block" and updated.usage == saved.usage
            assert updated.key_hashes == saved.key_hashes and updated.input_rule_actions == saved.input_rule_actions
        assert len(db.audit_items()) == 1
    finally:
        db.close()
