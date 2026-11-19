from dataclasses import dataclass
from fastapi import FastAPI
from fastapi.testclient import TestClient
from guardoath.admin_billing import AdminSubscriptionReporter, BillingUnavailable
from guardoath.admin_console import VerifiedAdmin, create_admin_router
import pytest

@dataclass
class Tenant:
    tenant_id: str = "usr-billing"
    payer_id: str | None = "cus_test1234"
    stripe_subscription_id: str | None = "sub_test1234"
    demo: bool = False

class Store:
    def get(self, tenant_id): return Tenant() if tenant_id=="usr-billing" else None


def snapshot(**updates):
    return {"id":"sub_test1234","customer":"cus_test1234","object":"subscription", "status":"active",
            "customer_email":"secret@example.com", "payment_method":"pm_private", **updates}


def test_verified_billing_view_masks_provider_data_and_does_not_change_entitlements():
    r=AdminSubscriptionReporter(lambda tid, cid, sid:snapshot()).read(Tenant())
    assert r["verified"] is True and r["subscription_entitlement"] is True
    assert "secret@example.com" not in str(r) and "cus_" not in str(r)


def test_no_binding_never_calls_provider():
    called=[]
    reporter=AdminSubscriptionReporter(lambda *args:called.append(args))
    assert reporter.read(Tenant(payer_id=None))["status"]=="unbound"
    assert reporter.read(Tenant(demo=True))["status"]=="not_applicable"
    assert not called


@pytest.mark.parametrize("state",["past_due","canceled","unpaid","incomplete","trialing","paused"])
def test_non_active_stripe_state_never_reports_paid_entitlement(state):
    x=AdminSubscriptionReporter(lambda *args:snapshot(status=state)).read(Tenant())
    assert x["verified"] and x["subscription_entitlement"] is False


@pytest.mark.parametrize("wrong",[
    {"customer":"cus_someoneelse"}, {"id":"sub_wrong"},{"status":"invented"},
    {"object":"invoice"},
])
def test_bad_provider_snapshot_fails_closed(wrong):
    with pytest.raises(BillingUnavailable):
        AdminSubscriptionReporter(lambda *args:snapshot(**wrong)).read(Tenant())


def test_billing_endpoint_denies_customer_and_preserves_data_boundary():
    app=FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token:VerifiedAdmin("trusted-subject",frozenset({"owner"}),True) if token=="admin" else None,
        tenant_store=Store(),billing_reporter=AdminSubscriptionReporter(lambda *args:snapshot())))
    cli=TestClient(app)
    path="/v1/admin/subscriptions/usr-billing"
    assert cli.get(path).status_code==401
    assert cli.get(path,headers={"X-GuardOath-Key":"admin"}).status_code==401
    response=cli.get(path,headers={"X-GuardOath-Admin-Session":"admin"})
    assert response.status_code==200
    assert "secret@example.com" not in response.text
    assert cli.get("/v1/admin/subscriptions/usr-notfound",headers={"X-GuardOath-Admin-Session":"admin"}).status_code==404
