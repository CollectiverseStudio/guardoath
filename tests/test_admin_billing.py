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
