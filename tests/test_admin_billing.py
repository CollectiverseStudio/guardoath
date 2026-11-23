"""Verified provider and price reporting must never manufacture paid access."""
from dataclasses import dataclass
from fastapi import FastAPI
from fastapi.testclient import TestClient
from guardoath.admin_billing import AdminSubscriptionReporter, BillingUnavailable
from guardoath.admin_console import VerifiedAdmin, create_admin_router
import pytest

PRICE = "price_plan123"


@dataclass
class Tenant:
    tenant_id: str = "usr-billing"
    payer_id: str | None = "cus_test1234"
    stripe_subscription_id: str | None = "sub_test1234"
    demo: bool = False
    status: str = "active"
    billing_mode: str = "stripe"
    subscription_status: str | None = "active"


class Store:
    def get(self, tenant_id): return Tenant() if tenant_id == "usr-billing" else None


def snapshot(**updates):
    return {"id": "sub_test1234", "customer": "cus_test1234", "object": "subscription",
            "status": "active", "livemode": False,
            "items": {"data": [{"price": {"id": PRICE}, "quantity": 1}], "has_more": False},
            "pause_collection": None,
            "customer_email": "secret@example.com", "payment_method": "pm_private", **updates}


def reporter(fn):
    return AdminSubscriptionReporter(fn, expected_price_id=PRICE, expected_livemode=False)


def test_verified_billing_view_masks_provider_data_and_does_not_change_entitlements():
    record = Tenant()
    result = reporter(lambda tid, cid, sid: snapshot()).read(record)
    assert result["verified"] is True and result["subscription_entitlement"] is True
    assert result["provider_active"] is True and result["price_verified"] is True
    assert "secret@example.com" not in str(result) and "cus_" not in str(result)
    assert record.subscription_status == "active"


def test_no_binding_never_calls_provider():
    called = []
    view = reporter(lambda *args: called.append(args))
    assert view.read(Tenant(payer_id=None))["status"] == "unbound"
    assert view.read(Tenant(demo=True))["status"] == "not_applicable"
    assert not called


@pytest.mark.parametrize("state", ["past_due", "canceled", "unpaid", "incomplete", "trialing", "paused"])
def test_non_active_stripe_state_never_reports_paid_entitlement(state):
    result = reporter(lambda *args: snapshot(status=state)).read(Tenant())
    assert result["verified"] and result["subscription_entitlement"] is False


@pytest.mark.parametrize("record", [
    Tenant(status="disabled"), Tenant(subscription_status="inactive"),
    Tenant(subscription_status="past_due"), Tenant(subscription_status=None),
    Tenant(billing_mode="internal"),
])
def test_active_provider_does_not_override_disabled_unpaid_or_unbound_tenant(record):
    result = reporter(lambda *args: snapshot()).read(record)
    assert result["provider_active"] and not result["subscription_entitlement"]


@pytest.mark.parametrize("wrong", [
    {"customer": "cus_someoneelse"}, {"id": "sub_wrong"}, {"status": "invented"},
    {"object": "invoice"}, {"livemode": True}, {"livemode": None},
    {"items": {"data": [{"price": "price_wrong", "quantity": 1}], "has_more": False}},
    {"items": {"data": [{"price": {"id": PRICE}, "quantity": 1}, {"price": {"id": PRICE}, "quantity": 1}], "has_more": False}},
    {"items": {"data": [{"price": {"id": PRICE}, "quantity": 1}], "has_more": True}},
    {"items": {"data": [{"price": {"id": PRICE}}], "has_more": False}},
    {"items": {"data": [{"price": {"id": PRICE}, "quantity": 0}], "has_more": False}},
    {"items": {"data": [{"price": {"id": PRICE}, "quantity": -1}], "has_more": False}},
    {"items": {"data": [{"price": {"id": PRICE}, "quantity": True}], "has_more": False}},
    {"items": {"data": [{"price": {"id": PRICE}, "quantity": "1"}], "has_more": False}},
    {"items": {"data": [{"price": {"id": PRICE}, "quantity": 1000001}], "has_more": False}},
    {"items": {}}, {"items": None},
])
def test_bad_provider_snapshot_fails_closed(wrong):
    with pytest.raises(BillingUnavailable):
        reporter(lambda *args: snapshot(**wrong)).read(Tenant())


@pytest.mark.parametrize("bad", ["", "price", "price_", "price_wrong-id", None])
def test_unconfigured_or_malformed_price_never_allows_provider_reads(bad):
    with pytest.raises(ValueError):
        AdminSubscriptionReporter(lambda *args: snapshot(), expected_price_id=bad, expected_livemode=False)


def test_billing_endpoint_denies_customer_and_preserves_data_boundary():
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin("trusted-subject", frozenset({"owner"}), True)
        if token == "admin" else None,
        tenant_store=Store(), billing_reporter=reporter(lambda *args: snapshot())))
    cli = TestClient(app)
    path = "/v1/admin/subscriptions/usr-billing"
    assert cli.get(path).status_code == 401
    assert cli.get(path, headers={"X-GuardOath-Key": "admin"}).status_code == 401
    response = cli.get(path, headers={"X-GuardOath-Admin-Session": "admin"})
    assert response.status_code == 200
    assert "secret@example.com" not in response.text
    assert response.json()["subscription"]["subscription_entitlement"] is True
    assert cli.get("/v1/admin/subscriptions/usr-notfound", headers={"X-GuardOath-Admin-Session": "admin"}).status_code == 404


def test_positive_subscription_quantity_preserves_read_only_verification():
    for quantity in (1, 2, 10):
        result = reporter(lambda *args: snapshot(items={"data": [
            {"price": {"id": PRICE}, "quantity": quantity}], "has_more": False})).read(Tenant())
        assert result["verified"] and result["provider_active"]
        assert result["subscription_entitlement"] is True


@pytest.mark.parametrize("provider_status,saved_status,tenant_status,billing_mode,expected_level,expected_code", [
    ("active", "active", "active", "stripe", "consistent", "states_agree"),
    ("past_due", "active", "active", "stripe", "critical", "saved_active_without_verified_provider"),
    ("canceled", "active", "active", "stripe", "critical", "saved_active_without_verified_provider"),
    ("unpaid", "active", "active", "stripe", "critical", "saved_active_without_verified_provider"),
    ("trialing", "active", "active", "stripe", "critical", "saved_active_without_verified_provider"),
    ("active", "inactive", "active", "stripe", "review", "provider_active_saved_inactive"),
    ("active", "past_due", "active", "stripe", "review", "provider_active_saved_inactive"),
    ("active", "active", "disabled", "stripe", "review", "provider_active_tenant_disabled"),
    ("active", "active", "active", "internal", "review", "provider_active_billing_mode_mismatch"),
    ("canceled", "canceled", "active", "stripe", "consistent", "states_agree"),
])
def test_owner_billing_reconciliation_diagnostic_is_read_only_and_grounded(
        provider_status, saved_status, tenant_status, billing_mode, expected_level, expected_code):
    record = Tenant(subscription_status=saved_status, status=tenant_status, billing_mode=billing_mode)
    before = dict(record.__dict__)
    result = reporter(lambda *_: snapshot(status=provider_status)).read(record)
    assert result["reconciliation"]["level"] == expected_level
    assert result["reconciliation"]["code"] == expected_code
    assert result["subscription_entitlement"] is (provider_status == "active" and
        saved_status == "active" and tenant_status == "active" and billing_mode == "stripe")
    assert record.__dict__ == before
    assert "pm_private" not in str(result) and "secret@example.com" not in str(result)
    assert "verified_at" in result and result["verified"] is True


@pytest.mark.parametrize("provider_status,saved_status,tenant_status,billing_mode,level,code", [
    ("canceled", "past_due", "disabled", "stripe", "review",
     "saved_nonactive_state_differs_from_provider"),
    ("past_due", "inactive", "disabled", "stripe", "review",
     "saved_nonactive_state_differs_from_provider"),
    ("unpaid", "canceled", "active", "stripe", "review",
     "saved_nonactive_state_differs_from_provider"),
    ("incomplete", "past_due", "disabled", "stripe", "review",
     "saved_nonactive_state_differs_from_provider"),
    ("canceled", "active", "disabled", "stripe", "critical",
     "saved_active_without_verified_provider"),
    ("past_due", "active", "disabled", "internal", "critical",
     "saved_active_without_verified_provider"),
    ("trialing", "inactive", "disabled", "stripe", "consistent", "states_agree"),
    ("paused", "inactive", "disabled", "stripe", "consistent", "states_agree"),
    ("canceled", "canceled", "active", "stripe", "consistent", "states_agree"),
])
def test_nonactive_provider_and_saved_billing_disagreements_are_never_called_consistent(
        provider_status, saved_status, tenant_status, billing_mode, level, code):
    record = Tenant(subscription_status=saved_status, status=tenant_status, billing_mode=billing_mode)
    before = dict(record.__dict__)
    reads = []
    result = reporter(lambda *args: (reads.append(args), snapshot(status=provider_status))[1]).read(record)
    assert reads == [(record.tenant_id, record.payer_id, record.stripe_subscription_id)]
    assert result["verified"] is True and result["provider_active"] is False
    assert result["subscription_entitlement"] is False
    assert (result["reconciliation"]["level"], result["reconciliation"]["code"]) == (level, code)
    assert record.__dict__ == before
    assert "secret@example.com" not in str(result)
    assert "cus_" not in str(result) and "pm_private" not in str(result)


def test_missing_provider_binding_flags_saved_active_entitlement_without_provider_call():
    calls = []
    stored_active = Tenant(payer_id=None, subscription_status="active")
    outcome = reporter(lambda *_: calls.append(1)).read(stored_active)
    assert outcome["status"] == "unbound" and not outcome["verified"]
    assert outcome["reconciliation"] == {
        "level": "critical", "code": "saved_active_without_verified_provider",
        "next_step": "Investigate trusted reconciliation; use audited tenant suspension if access must be blocked"}
    assert not calls
    innocent = reporter(lambda *_: calls.append(1)).read(Tenant(payer_id=None, subscription_status="inactive"))
    assert innocent["reconciliation"]["level"] == "unavailable"
    assert not calls


def test_authenticated_subscription_route_reports_critical_drift_without_sensitive_provider_data():
    provider_reads = []
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda t: VerifiedAdmin("trusted-subject", frozenset({"owner"}), True)
        if t == "admin" else None, tenant_store=Store(),
        billing_reporter=reporter(lambda *a: (provider_reads.append(a), snapshot(status="canceled"))[1])))
    path = "/v1/admin/subscriptions/usr-billing"
    c = TestClient(app)
    assert c.get(path).status_code == 401
    assert c.get(path, headers={"X-GuardOath-Key": "admin"}).status_code == 401
    assert provider_reads == []
    actual = c.get(path, headers={"X-GuardOath-Admin-Session": "admin"})
    assert actual.status_code == 200
    assert actual.json()["subscription"]["reconciliation"]["level"] == "critical"
    assert len(provider_reads) == 1
    for secret in ("cus_test1234", "sub_test1234", "pm_private", "secret@example.com"):
        assert secret not in actual.text


def test_owner_billing_period_end_and_cancellation_are_verified_provider_advisories():
    from datetime import datetime, timezone
    stamp = 1796083200  # 2026-12-01 UTC; no inference from server clock
    view = reporter(lambda *_: snapshot(current_period_end=stamp, cancel_at_period_end=True)).read(Tenant())
    assert view["verified"] is True and view["cancel_at_period_end"] is True
    assert view["current_period_end_utc"] == datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert view["subscription_entitlement"] is True
    assert "invoice" in view["billing_cycle_source"]
    assert "latest_invoice" not in view and "invoice_amount" not in view
    assert "cus_" not in str(view) and "pm_private" not in str(view)


@pytest.mark.parametrize("value", [None, True, -1, "1796083200", 1.5, 4102444801])
def test_invalid_optional_stripe_cycle_timestamp_is_unknown_not_fabricated(value):
    view = reporter(lambda *_: snapshot(current_period_end=value, cancel_at_period_end="true")).read(Tenant())
    assert view["verified"] is True and view["current_period_end_utc"] is None
    assert view["cancel_at_period_end"] is None
    assert view["subscription_entitlement"] is True  # optional schedule metadata cannot modify entitlement


def test_unbound_subscription_never_gets_fabricated_schedule_or_provider_read():
    reads = []
    view = reporter(lambda *_: reads.append(1)).read(Tenant(payer_id=None))
    assert not view["verified"] and "current_period_end_utc" not in view
    assert not reads

@pytest.mark.parametrize("field, malformed", [
    ("demo", None), ("demo", "false"), ("demo", 0),
    ("subscription_status", []), ("subscription_status", {"status": "active"}),
    ("subscription_status", False), ("subscription_status", "suspended"),
    ("status", []), ("status", True), ("status", "suspended"),
    ("billing_mode", {}), ("billing_mode", "unverified"),
    ("payer_id", []), ("payer_id", "cus_"),
    ("stripe_subscription_id", {}), ("stripe_subscription_id", "sub_"),
    ("tenant_id", []), ("tenant_id", "example-tenant"),
])
def test_corrupt_saved_identity_or_billing_state_never_calls_stripe(field, malformed):
    record = Tenant()
    setattr(record, field, malformed)
    reads = []
    with pytest.raises(BillingUnavailable):
        reporter(lambda *args: (reads.append(args), snapshot())[1]).read(record)
    assert reads == []


@pytest.mark.parametrize("malformed", [None, [], {}, {"status": "active"}, True, 17, ["active"]])
def test_malformed_provider_status_is_unavailable_without_entitlement(malformed):
    record = Tenant()
    with pytest.raises(BillingUnavailable):
        reporter(lambda *_: snapshot(status=malformed)).read(record)
    assert record.subscription_status == "active"


def test_authenticated_owner_provider_shape_failure_is_sanitized_503():
    reads = []
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(
            "trusted-subject", frozenset({"owner"}), True) if token == "admin" else None,
        tenant_store=Store(),
        billing_reporter=reporter(lambda *args: (
            reads.append(args),
            snapshot(status={"private": "secret@example.com"})
        )[1]),
    ))
    client = TestClient(app)
    endpoint = "/v1/admin/subscriptions/usr-billing"
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers={"X-GuardOath-Key": "admin"}).status_code == 401
    assert reads == []
    response = client.get(endpoint, headers={"X-GuardOath-Admin-Session": "admin"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Subscription verification unavailable"
    assert len(reads) == 1
    for private in ("secret@example.com", "cus_test1234", "sub_test1234", "pm_private"):
        assert private not in response.text



@pytest.mark.parametrize("behavior", ["keep_as_draft", "mark_uncollectible", "void"])
def test_verified_active_subscription_with_paused_collection_requires_review(behavior):
    from datetime import datetime, timezone
    record = Tenant()
    before = dict(record.__dict__)
    stamp = 1796083200
    result = reporter(lambda *_: snapshot(
        pause_collection={"behavior": behavior, "resumes_at": stamp})).read(record)
    assert result["verified"] is True and result["provider_active"] is True
    assert result["subscription_entitlement"] is True  # Reporting never revokes/grants access.
    assert result["collection_status"] == "paused"
    assert result["collection_behavior"] == behavior
    assert result["collection_resumes_at_utc"] == datetime.fromtimestamp(
        stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert result["reconciliation"]["level"] == "review"
    assert result["reconciliation"]["code"] == "stripe_payment_collection_paused"
    assert record.__dict__ == before
    for private in ("cus_test1234", "sub_test1234", "secret@example.com", "pm_private"):
        assert private not in str(result)


def test_indefinite_pause_and_absent_collection_field_remain_distinct():
    paused = reporter(lambda *_: snapshot(
        pause_collection={"behavior": "void", "resumes_at": None})).read(Tenant())
    assert paused["collection_status"] == "paused"
    assert paused["collection_resumes_at_utc"] is None
    assert paused["reconciliation"]["level"] == "review"
    absent = snapshot()
    del absent["pause_collection"]
    unknown = reporter(lambda *_: absent).read(Tenant())
    assert unknown["collection_status"] == "unavailable"
    assert unknown["collection_behavior"] is None
    assert unknown["collection_resumes_at_utc"] is None
    assert reporter(lambda *_: snapshot()).read(Tenant())["collection_status"] == "collecting"


@pytest.mark.parametrize("bad", [
    False, [], "", 17, {},
    {"behavior": None}, {"behavior": ["void"]}, {"behavior": "unknown"},
    {"behavior": "void", "resumes_at": True},
    {"behavior": "void", "resumes_at": "1796083200"},
    {"behavior": "void", "resumes_at": -1},
    {"behavior": "void", "resumes_at": 4102444801},
])
def test_malformed_collection_pause_fails_closed_without_claiming_consistency(bad):
    with pytest.raises(BillingUnavailable):
        reporter(lambda *_: snapshot(pause_collection=bad)).read(Tenant())


def test_authenticated_owner_sees_collection_pause_risk_without_provider_secrets():
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(
            "trusted-subject", frozenset({"owner"}), True) if token == "admin" else None,
        tenant_store=Store(), billing_reporter=reporter(lambda *_: snapshot(
            pause_collection={"behavior": "keep_as_draft", "resumes_at": None}))))
    endpoint = "/v1/admin/subscriptions/usr-billing"
    client = TestClient(app)
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers={"X-GuardOath-Key": "admin"}).status_code == 401
    response = client.get(endpoint, headers={"X-GuardOath-Admin-Session": "admin"})
    assert response.status_code == 200
    subscription = response.json()["subscription"]
    assert subscription["collection_status"] == "paused"
    assert subscription["reconciliation"]["level"] == "review"
    for private in ("cus_test1234", "sub_test1234", "secret@example.com", "pm_private"):
        assert private not in response.text
