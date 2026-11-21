"""Authenticated owner invoice history, provider binding and fail-closed cases."""
from dataclasses import dataclass
import io
import json
import urllib.parse
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from guardoath.admin_billing import AdminSubscriptionReporter
from guardoath.admin_audit import AdminSensitiveReadAuditor
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.admin_invoices import AdminInvoiceHistory, InvoiceHistoryUnavailable, fetch_invoice_list
from guardoath.subscription_reconciliation import StripeBinding

@dataclass
class Tenant:
    tenant_id: str = "usr-invoices"
    payer_id: str | None = "cus_owner123"
    stripe_subscription_id: str | None = "sub_owner123"
    demo: bool = False
    status: str = "active"
    billing_mode: str = "stripe"
    subscription_status: str = "active"

class Store:
    def get(self, tenant_id):
        return Tenant() if tenant_id == "usr-invoices" else None

def subscription(**changes):
    return {"id": "sub_owner123", "customer": "cus_owner123", "object": "subscription",
            "status": "active", "livemode": False,
            "items": {"data": [{"price": {"id": "price_plan123"}, "quantity": 1}],
                      "has_more": False}, **changes}

def invoice(**changes):
    return {"id": "in_owner123", "object": "invoice", "customer": "cus_owner123",
            "subscription": "sub_owner123", "livemode": False, "status": "paid",
            "currency": "usd", "created": 1795161600, "amount_due": 499,
            "amount_paid": 499, "amount_remaining": 0, "customer_email": "secret@example.com",
            "hosted_invoice_url": "https://secret", **changes}

def history(fetch=None, sub_fetch=None):
    reporter = AdminSubscriptionReporter(sub_fetch or (lambda *_: subscription()),
        expected_price_id="price_plan123", expected_livemode=False)
    return AdminInvoiceHistory(fetch or (lambda *_: {
        "object": "list", "data": [invoice()], "has_more": False}), reporter)

def test_verified_history_is_redacted_read_only_minor_units():
    record = Tenant()
    result = history().read(record)
    assert result["count"] == 1 and result["verified"] and result["invoices"][0]["amount_paid_minor"] == 499
    assert "secret@example.com" not in str(result) and "https://secret" not in str(result)
    assert "cus_owner" not in str(result) and "in_owner" not in str(result)
    assert record.subscription_status == "active"

def test_unbound_and_demo_skip_invoice_provider():
    calls = []
    h = history(fetch=lambda *_: calls.append(1))
    assert not h.read(Tenant(payer_id=None))["verified"]
    assert not h.read(Tenant(demo=True))["verified"]
    assert not calls

@pytest.mark.parametrize("changes", [
    {"id":"in_slash/invalid"}, {"customer":"cus_other"}, {"subscription":"sub_other"},
    {"subscription":None}, {"livemode":True}, {"livemode":None}, {"object":"charge"},
    {"status":"refunded"}, {"currency":"USD"}, {"amount_due":-1},
    {"amount_paid":1.2}, {"amount_remaining":True}, {"created":0}, {"created":"bad"},
])
def test_mismatched_invoice_rejected(changes):
    with pytest.raises(InvoiceHistoryUnavailable):
        history(fetch=lambda *_: {"object":"list","data":[invoice(**changes)],
                                   "has_more":False}).read(Tenant())

@pytest.mark.parametrize("payload", [
    {"object":"charge","data":[],"has_more":False},
    {"object":"list","data":[],"has_more":"false"},
    {"object":"list","data":[invoice()]*6,"has_more":False},
    {"object":"list","data":[invoice(),invoice()],"has_more":False},
])
def test_ambiguous_invoice_page_rejected(payload):
    with pytest.raises(InvoiceHistoryUnavailable):
        history(fetch=lambda *_: payload).read(Tenant())

def test_subscription_mismatch_prevents_invoice_fetch():
    calls = []
    with pytest.raises(InvoiceHistoryUnavailable):
        history(fetch=lambda *_: calls.append(1),
                sub_fetch=lambda *_: subscription(customer="cus_wrong")).read(Tenant())
    assert not calls

def test_provider_fetch_is_scoped_and_no_secret_in_url():
    class Response(io.BytesIO):
        status = 200
    seen = []
    def fake_open(req,timeout):
        seen.append((req.full_url, req.get_header("Authorization"), timeout))
        return Response(json.dumps({"object":"list","data":[],"has_more":False}).encode())
    binding = StripeBinding("usr-invoices","cus_owner123","sub_owner123")
    assert fetch_invoice_list(binding,"sk_test_"+"x"*24,opener=fake_open)["object"] == "list"
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(seen[0][0]).query) == {
        "customer":["cus_owner123"],"subscription":["sub_owner123"],"limit":["5"]}
    assert "sk_test_" not in seen[0][0] and seen[0][1].startswith("Basic ") and seen[0][2] == 5.0

def test_authorized_route_and_fail_closed_persistent_audit():
    calls = []
    def provider(*_):
        calls.append(1)
        return {"object":"list","data":[invoice()],"has_more":False}
    auth = lambda t: VerifiedAdmin("trusted-subject",frozenset({"owner"}),True) if t=="admin" else None
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=auth,
        tenant_store=Store(),billing_invoices=history(fetch=provider)))
    cli = TestClient(app)
    path = "/v1/admin/subscriptions/usr-invoices/invoices"
    assert cli.get(path).status_code==401
    assert cli.get(path,headers={"X-GuardOath-Key":"admin"}).status_code==401
    assert cli.get(path,headers={"X-GuardOath-Admin-Session":"admin"}).status_code==200
    assert calls==[1]
    assert cli.get("/v1/admin/subscriptions/usr-notfound/invoices",
                   headers={"X-GuardOath-Admin-Session":"admin"}).status_code==404
    class BrokenAudit:
        def put_item(self,**kwargs):
            raise RuntimeError("audit offline")
    app2 = FastAPI()
    app2.include_router(create_admin_router(verify_cognito_admin=auth,tenant_store=Store(),
        billing_invoices=history(fetch=provider),
        read_auditor=AdminSensitiveReadAuditor(
            BrokenAudit(),"audit-table",signing_key=b"s"*32),
        require_persistent_read_audit=True))
    assert TestClient(app2).get(path,headers={"X-GuardOath-Admin-Session":"admin"}).status_code==503
    assert calls==[1]
