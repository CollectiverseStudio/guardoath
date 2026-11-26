"""Actual reviewed restoration across provider, tenant and signed state boundaries."""
from copy import deepcopy
import json

import pytest

from admin_cap_restore_support import ACTOR, OTHER, TENANT, NOW, KEY, request, review, service, tenant
from guardoath import state_contracts
from guardoath.admin_actions import AdminTenantActions
from guardoath.admin_audit import AdminAuditReader
from guardoath.admin_cap_restore import AdminRequestCapRestoration, CapRestoreConflict, CapRestoreUnavailable
from guardoath.admin_cap_restore_state import DynamoCapRestoreState
from guardoath.dynamodb_store import DynamoDBTenantStore, to_item
from guardoath.tenant_record import TenantRecord


@pytest.fixture
def fixture(monkeypatch, tmp_path):
    from admin_test_support import AdminTestDynamo
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", True)
    db = AdminTestDynamo(tmp_path / "cap-service.sqlite3")
    actions, _, boundary = service(db)
    try:
        yield actions, db, boundary
    finally:
        db.close()


def raw(db):
    return db.get_item(TableName="tenant_table", Key={"tenant_id": {"S": TENANT}})["Item"]


def replace_fields(db, **changes):
    item = raw(db)
    fields = json.loads(item["record"]["S"])
    for key, value in changes.items():
        if value is ...:
            fields.pop(key, None)
        else:
            fields[key] = value
    item["record"]["S"] = json.dumps(fields)
    db.seed("tenant_table", item)


@pytest.mark.parametrize("status", ["active", "disabled"])
@pytest.mark.parametrize("cap", [0, 4000])
def test_restoration_changes_only_explicit_cap_allowance_timestamp_and_revision(fixture, status, cap):
    actions, db, boundary = fixture
    replace_fields(db, status=status, monthly_allowance=cap, admin_monthly_allowance_cap=cap)
    before = raw(db)
    reviewed = review(actions)
    assert reviewed["restore_available"] is True and reviewed["tenant_mutated"] is False
    assert reviewed["allowance_before"] == cap and reviewed["allowance_after"] == 10000
    assert reviewed["tenant_status"] == status
    result = actions.apply(**request(reviewed))
    after = raw(db)
    expected = json.loads(before["record"]["S"])
    expected.pop("admin_monthly_allowance_cap")
    expected.update(monthly_allowance=10000, updated_at=result["recorded_at_utc"])
    assert json.loads(after["record"]["S"]) == expected
    assert {k:v for k,v in after.items() if k not in ("record", "revision")} == {
        k:v for k,v in before.items() if k not in ("record", "revision")}
    assert after["revision"] == {"N":"4"} and db.transactions == 1
    assert len(boundary.calls) == 2 and result["replayed"] is False
    signed = AdminAuditReader(db, "audit_table", verify_key=KEY).read(result["audit_event_id"])
    assert signed["integrity_status"] == "signed_verified" and signed["operation"] == "tenant.allowance_restore"
    for private in ("cus_restore", "sub_restore", "price_plan", "si_restore"):
        assert private not in json.dumps([reviewed, result, db.audit_items()])


@pytest.mark.parametrize("changes", [
    {"admin_monthly_allowance_cap":...}, {"admin_monthly_allowance_cap":None},
    {"admin_monthly_allowance_cap":True}, {"admin_monthly_allowance_cap":4000.0},
    {"admin_monthly_allowance_cap":4001}, {"monthly_allowance":3999},
    {"monthly_allowance":True}, {"stripe_plan_monthly_allowance":...},
    {"stripe_plan_monthly_allowance":10001}, {"stripe_plan_monthly_allowance":10000.0},
    {"monthly_allowance":10000, "admin_monthly_allowance_cap":10000},
    {"subscription_status":"past_due"}, {"subscription_status":None},
    {"demo":True}, {"demo":0}, {"billing_mode":"internal"},
    {"status":...}, {"overage":"allow_and_record"},
    {"usage":{}}, {"revision":3},
])
def test_missing_or_incoherent_saved_cap_refuses_before_provider(fixture, changes):
    actions, db, boundary = fixture
    replace_fields(db, **changes)
    with pytest.raises(CapRestoreConflict):
        review(actions)
    assert not boundary.calls and db.transactions == 0 and not db.audit_items()


@pytest.mark.parametrize("field,value", [("payer_id",None),("stripe_subscription_id",None),
                                        ("payer_id","cus_bad/escape"),("stripe_subscription_id","sub_bad/escape")])
def test_no_binding_never_attempts_provider_discovery(fixture, field, value):
    actions, db, boundary = fixture
    replace_fields(db, **{field:value})
    with pytest.raises((CapRestoreConflict, CapRestoreUnavailable)):
        review(actions)
    assert not boundary.calls and db.transactions == 0


@pytest.mark.parametrize("change", ["status", "schedule", "period", "item", "cancellation", "price"])
def test_fresh_provider_change_consumes_no_local_write(fixture, change):
    actions, db, boundary = fixture
    prepared = request(review(actions))
    if change == "status": boundary.value["status"] = "past_due"
    elif change == "schedule": boundary.value["schedule"] = "sub_sched_changed"
    elif change == "period": boundary.value["current_period_end"] += 10
    elif change == "item": boundary.value["items"]["data"][0]["id"] = "si_changed"
    elif change == "cancellation": boundary.value["cancel_at_period_end"] = True
    else: boundary.value["items"]["data"][0]["price"]["id"] = "price_other"
    with pytest.raises(CapRestoreConflict):
        actions.apply(**prepared)
    assert db.transactions == 0 and raw(db)["revision"] == {"N":"3"}


def test_supported_future_cancellation_is_reviewed_and_preserved(fixture):
    actions, db, boundary = fixture
    boundary.value.update(cancel_at_period_end=True, cancel_at=NOW+86400)
    original = deepcopy(boundary.value)
    prepared = review(actions)
    assert prepared["cancellation"]["cancel_at_period_end"] is True
    assert prepared["cancellation"]["cancel_at_utc"] == prepared["current_period_end_utc"]
    actions.apply(**request(prepared))
    assert boundary.value == original and len(boundary.calls) == 2


@pytest.mark.parametrize("change", ["document", "revision", "usage"])
def test_exact_document_cas_rejects_races_but_keeps_concurrent_usage(fixture, change):
    actions, db, boundary = fixture
    prepared = request(review(actions))
    def concurrent(_binding):
        item = raw(db)
        if change == "document":
            body = json.loads(item["record"]["S"])
            body["status"] = "disabled"
            item["record"]["S"] = json.dumps(body)
        elif change == "revision": item["revision"] = {"N":"4"}
        else: item["usage"]["M"]["2026-11"] = {"N":"232"}
        db.seed("tenant_table", item)
    boundary.before = concurrent
    if change == "usage":
        assert actions.apply(**prepared)["status"] == "applied"
        assert raw(db)["usage"]["M"]["2026-11"] == {"N":"232"}
    else:
        with pytest.raises(CapRestoreConflict): actions.apply(**prepared)
        assert db.transactions == 0 and not db.audit_items()


def test_changed_document_without_revision_is_rejected_before_second_provider_read(fixture):
    actions, db, boundary = fixture
    prepared = request(review(actions))
    replace_fields(db, display_name="Concurrent account edit")
    with pytest.raises(CapRestoreConflict): actions.apply(**prepared)
    assert len(boundary.calls) == 1 and db.transactions == 0


def test_lost_commit_response_recovers_once_and_expired_replay_never_restores_a_later_cap(fixture, monkeypatch):
    actions, db, boundary = fixture
    prepared = request(review(actions))
    db.lose_transaction_response = True
    applied = actions.apply(**prepared)
    assert applied["replayed"] is True and db.transactions == 1
    db.lose_transaction_response = False
    AdminTenantActions(db, "tenant_table", "audit_table", audit_key=KEY).cap_monthly_allowance(
        actor=ACTOR, tenant_id=TENANT, expected_revision=4, new_limit=1200, confirmation=TENANT,
        reason="Apply later owner request budget", idempotency_key="later-cap-reference-1234567",
        validate_record=TenantRecord.model_validate)
    later = raw(db)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", False)
    def no_clock(): pytest.fail("Historical replay must not consult time or current entitlement")
    history = AdminRequestCapRestoration(state=DynamoCapRestoreState(db, "audit_table", KEY),
        tenant_table="tenant_table", preview_key=b"changed-preview-key-0123456789abcdef", clock=no_clock)
    assert history.provider_configured is False
    assert history.apply(**prepared) == applied
    assert history.receipt(actor=ACTOR, tenant_id=TENANT, idempotency_key=prepared["idempotency_key"]) == applied
    assert raw(db) == later and len(boundary.calls) == 2 and db.transactions == 2


def test_unresolved_transaction_has_one_attempt_and_receipt_lookup_is_read_only(fixture, monkeypatch):
    actions, db, boundary = fixture
    prepared = request(review(actions))
    attempted = []
    def unavailable(**kwargs):
        attempted.append(kwargs)
        raise TimeoutError("Synthetic unknown outcome")
    monkeypatch.setattr(db, "transact_write_items", unavailable)
    with pytest.raises(CapRestoreUnavailable): actions.apply(**prepared)
    assert len(attempted) == 1
    result = actions.receipt(actor=ACTOR, tenant_id=TENANT, idempotency_key=prepared["idempotency_key"])
    assert result["status"] == "not_found" and result["tenant_mutated"] is False
    assert len(attempted) == 1 and len(boundary.calls) == 2


@pytest.mark.parametrize("field,value", [("reason","Changed reason for same operation"),
    ("expected_revision",4),("confirmation","Different confirmation"),("tenant_id","usr-other"),
    ("preview_reference","cr1.changed")])
def test_reused_idempotency_reference_cannot_change_the_request(fixture, field, value):
    actions, db, boundary = fixture
    prepared = request(review(actions))
    actions.apply(**prepared)
    with pytest.raises(CapRestoreConflict): actions.apply(**{**prepared, field:value})
    assert len(boundary.calls) == 2 and db.transactions == 1


@pytest.mark.parametrize("context", ["actor", "tenant", "confirmation", "token", "revision", "plan", "price", "mode"])
def test_review_is_bound_to_owner_tenant_config_and_exact_confirmation(fixture, context):
    actions, db, boundary = fixture
    prepared = request(review(actions))
    if context == "actor": prepared["actor"] = OTHER
    elif context == "tenant": prepared["tenant_id"] = "usr-other"
    elif context == "confirmation": prepared["confirmation"] += " "
    elif context == "token":
        token = prepared["preview_reference"]
        prepared["preview_reference"] = token[:-1] + ("A" if token[-1] != "A" else "B")
    elif context == "revision": prepared["expected_revision"] = 4
    elif context == "plan": actions.plan_monthly_allowance = 12000
    elif context == "price": actions.expected_price_id = "price_new"
    else: actions.expected_livemode = True
    with pytest.raises(CapRestoreConflict): actions.apply(**prepared)
    assert len(boundary.calls) == 1 and db.transactions == 0


@pytest.mark.parametrize("elapsed", [180,181,-1])
def test_expired_or_future_review_has_no_second_provider_read(fixture, elapsed):
    actions, db, boundary = fixture
    prepared = request(review(actions))
    actions._clock = lambda: NOW + elapsed
    with pytest.raises(CapRestoreConflict): actions.apply(**prepared)
    assert len(boundary.calls) == 1 and db.transactions == 0


def test_period_boundary_shortens_review_and_expiry_during_read_refuses_commit(fixture):
    actions, db, boundary = fixture
    boundary.value["current_period_end"] = NOW + 20
    prepared = review(actions)
    assert prepared["expires_at_utc"] == prepared["current_period_end_utc"]
    # The direct read completed within the period, but dispatch is no longer current.
    clock = iter([NOW+19, NOW+19, NOW+20])
    actions._clock = lambda: next(clock)
    with pytest.raises(CapRestoreConflict): actions.apply(**request(prepared))
    assert db.transactions == 0


def test_slow_final_history_read_cannot_commit_an_expired_review(fixture, monkeypatch):
    actions, db, boundary = fixture
    boundary.value["current_period_end"] = NOW + 20
    prepared = request(review(actions))
    clock = [NOW]
    actions._clock = lambda: clock[0]
    original = actions.state.history
    reads = []
    def delayed_history(*args, **kwargs):
        result = original(*args, **kwargs)
        reads.append(args)
        if len(reads) == 2:
            clock[0] = NOW + 20
        return result
    monkeypatch.setattr(actions.state, "history", delayed_history)
    with pytest.raises(CapRestoreConflict): actions.apply(**prepared)
    assert len(reads) == 2 and len(boundary.calls) == 2 and db.transactions == 0
    assert not db.audit_items()


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "1795700000", 0])
def test_invalid_server_clock_never_reaches_provider(fixture, value):
    actions, db, boundary = fixture
    actions._clock = lambda:value
    with pytest.raises(CapRestoreUnavailable): review(actions)
    assert not boundary.calls and db.transactions == 0


def test_elapsed_operation_budget_refuses_after_provider_without_retry(fixture, monkeypatch):
    from guardoath import admin_cap_restore
    actions, db, boundary = fixture
    prepared = request(review(actions))
    ticks = iter([10.0, 10.1, 19.0])
    monkeypatch.setattr(admin_cap_restore.time, "monotonic", lambda:next(ticks))
    with pytest.raises(CapRestoreUnavailable): actions.apply(**prepared)
    assert len(boundary.calls) == 2 and db.transactions == 0


def test_reader_can_preview_and_read_history_but_never_write_even_with_environment_override(fixture, monkeypatch):
    actions, db, boundary = fixture
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", False)
    monkeypatch.setenv("GUARDOATH_ENABLE_NEW_CAP_RESTORATION_STATE", "1")
    prepared = review(actions)
    assert prepared["restore_available"] is False
    with pytest.raises(CapRestoreUnavailable): actions.apply(**request(prepared))
    assert len(boundary.calls) == 1 and db.transactions == 0


def test_provider_reads_are_bounded_without_limiting_signed_history(fixture):
    actions, db, boundary = fixture
    for _ in range(20): review(actions)
    with pytest.raises(CapRestoreUnavailable): review(actions)
    assert len(boundary.calls) == 20
    assert actions.receipt(actor=ACTOR, tenant_id=TENANT, idempotency_key="unknown-reference-123456789")["status"] == "not_found"
    actions._clock = lambda: NOW+60
    review(actions)
    assert len(boundary.calls) == 21
