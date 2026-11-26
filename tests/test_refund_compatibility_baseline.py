"""A refund reader supports recovery before a later source-controlled writer."""
import importlib.util
import itertools
from pathlib import Path

import pytest

from admin_test_support import AdminTestDynamo
from guardoath import state_contracts
from guardoath.admin_cancellation_provider import digest
from guardoath.admin_refund_state import CONTRACT, DynamoRefundState, RefundStateUnavailable
from test_admin_refund_state import NOW, KEY, TENANT, fields, make, proof, reserved, dispatched, stored_tenant
from test_rollback_compatibility import archive, gate, metadata, verify, SHA, OLD

PRIOR_CONTRACTS = (
    "subscription-cap-provenance-v1", "stripe-period-end-cancellation-v1",
    "catalog-publication-v1", "catalog-publication-lifecycle-v1",
)
FLAGS = (
    "ENABLE_NEW_SUBSCRIPTION_STATE", "ENABLE_NEW_CANCELLATION_STATE",
    "ENABLE_NEW_CATALOG_PUBLICATION_STATE", "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE",
    "ENABLE_NEW_REFUND_STATE",
)


def test_source_default_adds_reader_support_without_new_refund_writes_or_env_override(monkeypatch):
    for name in ("ENABLE_NEW_REFUND_STATE", "GUARDOATH_ENABLE_NEW_REFUND_STATE",
                 "GUARDOATH_REFUND_WRITER_ENABLED", "GUARDOATH_ADMIN_TEST_WRITER_PHASE"):
        monkeypatch.setenv(name, "1")
    assert state_contracts.ENABLE_NEW_REFUND_STATE is False
    assert state_contracts.new_refund_state_enabled() is False
    declaration = state_contracts.release_metadata(SHA)
    assert declaration["supported_state_contracts"] == [*PRIOR_CONTRACTS, CONTRACT]
    assert declaration["introduced_state_contracts"] == list(PRIOR_CONTRACTS)


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", [], {}])
def test_refund_gate_requires_source_boolean(monkeypatch, invalid):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", invalid)
    with pytest.raises(ValueError): state_contracts.new_refund_state_enabled()
    with pytest.raises(ValueError): state_contracts.release_metadata(SHA)


@pytest.mark.parametrize("enabled", list(itertools.product([False, True], repeat=5)))
def test_all_five_writer_introductions_remain_independent(monkeypatch, enabled):
    for flag, value in zip(FLAGS, enabled):
        monkeypatch.setattr(state_contracts, flag, value)
    contracts = (*PRIOR_CONTRACTS, CONTRACT)
    declaration = state_contracts.release_metadata(SHA)
    assert declaration["supported_state_contracts"] == list(contracts)
    assert declaration["introduced_state_contracts"] == [name for name, value in zip(contracts, enabled) if value]


def test_actual_artifact_reader_accepts_current_backup_but_writer_requires_refund_reader(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("refund_build", root / "deploy/aws/build_lambda.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    reader_declaration = build.release_metadata(SHA)
    assert reader_declaration == state_contracts.release_metadata(SHA)
    old = archive(tmp_path / "current-production.zip", metadata(OLD, support=PRIOR_CONTRACTS, writes=PRIOR_CONTRACTS))
    reader = archive(tmp_path / "refund-reader.zip", reader_declaration)
    assert verify(reader, old)["status"] == "compatible"
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", True)
    writer = archive(tmp_path / "refund-writer.zip", state_contracts.release_metadata(SHA))
    with pytest.raises(ValueError, match="compatibility baseline"):
        verify(writer, old)
    accepted_reader = archive(tmp_path / "accepted-refund-reader.zip", {**reader_declaration, "source_sha": OLD})
    assert verify(writer, accepted_reader)["status"] == "compatible"
    dropped = archive(tmp_path / "dropped-refund-reader.zip", metadata(SHA, support=PRIOR_CONTRACTS, writes=PRIOR_CONTRACTS))
    with pytest.raises(ValueError, match="drops deployed"):
        verify(dropped, accepted_reader)
    assert gate.archive_digest(accepted_reader) != gate.archive_digest(old)


@pytest.mark.parametrize("phase", ["reserved", "dispatching", "indeterminate", "pending", "verified", "failed"])
def test_each_persisted_phase_reopens_after_reader_rollback_without_allowing_writes(tmp_path, monkeypatch, phase):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", True)
    path = tmp_path / (phase + ".sqlite3")
    state, db = make(AdminTestDynamo(path))
    control, intent = reserved(state)
    if phase != "reserved":
        control = dispatched(state, db, control)
    if phase not in ("reserved", "dispatching"):
        if phase in ("verified", "failed"):
            state.outcome(intent, "indeterminate", NOW)
            state.observe(intent, "pending", NOW + 1, proof(intent, "pending"))
            state.observe(intent, phase, NOW + 2, proof(intent, "succeeded" if phase == "verified" else "failed"))
        else:
            state.outcome(intent, phase, NOW, None if phase == "indeterminate" else proof(intent, "pending"))
    original = state.history(intent["action_id"])
    tenant_before, rows_before = stored_tenant(db), db.audit_items()
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", False)
    reopened = AdminTestDynamo(path)
    try:
        reader = DynamoRefundState(reopened, "audit_table", KEY)
        assert reader.history(intent["action_id"]) == original
        for operation in (
            lambda: reader.reserve(fields(action_id="d" * 64), expected_control_hash=digest(None)),
            lambda: reader.dispatching(control, tenant_table="tenant_table", tenant_id=TENANT,
                                      expected_revision=3, original_document=tenant_before["record"]["S"]),
            lambda: reader.outcome(intent, "verified", NOW + 3, proof(intent)),
            lambda: reader.observe(intent, "verified", NOW + 3, proof(intent)),
        ):
            with pytest.raises(RefundStateUnavailable): operation()
        assert stored_tenant(reopened) == tenant_before
        assert reopened.audit_items() == rows_before
    finally:
        reopened.close()
        db.close()
