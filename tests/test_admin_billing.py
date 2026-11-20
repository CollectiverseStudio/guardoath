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
