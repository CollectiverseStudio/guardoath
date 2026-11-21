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
    {"status":"refunded"}, {"status":[]}, {"status":{"open":True}},
    {"currency":"USD"}, {"amount_due":-1},
    {"collection_method":"unknown"}, {"collection_method":[]},
    {"attempted":1}, {"attempted":"false"},
    {"attempt_count":True}, {"attempt_count":-1}, {"attempt_count":10001},
    {"attempt_count":"2"}, {"next_payment_attempt":False},
    {"next_payment_attempt":"1795161600"}, {"next_payment_attempt":9999999999},
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

def test_payment_review_is_only_for_verified_first_page_and_keeps_currencies_separate():
    invoices = [
        invoice(id="in_us_open", status="open", amount_due=1700,
                amount_paid=200, amount_remaining=1500),
        invoice(id="in_eu_open", status="open", currency="eur",
                amount_due=2500, amount_paid=500, amount_remaining=2000),
        invoice(id="in_us_written", status="uncollectible", amount_due=800,
                amount_paid=0, amount_remaining=800),
        invoice(id="in_us_paid", status="paid", amount_due=400,
                amount_paid=400, amount_remaining=0),
    ]
    result = history(fetch=lambda *_: {"object": "list", "data": invoices,
                                        "has_more": True}).read(Tenant())
    review = result["payment_review"]
    assert review["scope"] == "verified_first_page_only"
    assert review["open_with_balance_count"] == 2
    assert review["uncollectible_count"] == 1
    assert review["open_balance_by_currency_minor"] == {"eur": 2000, "usd": 1500}
    assert review["complete_history"] is False
    assert "overdue" in review["note"] and "failed payment" in review["note"]
    assert result["has_more"] is True
    assert "in_us_" not in str(result) and "cus_owner" not in str(result)


def test_verified_invoice_collection_attempts_are_redacted_and_not_failure_claims():
    result = history(fetch=lambda *_: {"object": "list", "has_more": False, "data": [
        invoice(id="in_open_attempts", status="open", amount_due=2500,
                amount_paid=0, amount_remaining=2500,
                collection_method="charge_automatically", attempted=True,
                attempt_count=2, next_payment_attempt=1795435200),
        invoice(id="in_sent", status="open", amount_due=1000, amount_paid=0,
                amount_remaining=1000, collection_method="send_invoice",
                attempted=False, attempt_count=0, next_payment_attempt=None),
    ]}).read(Tenant())
    recorded, manual = result["invoices"]
    assert recorded["collection_method"] == "charge_automatically"
    assert recorded["attempted"] is True and recorded["attempt_count"] == 2
    assert recorded["next_payment_attempt_utc"] == "2026-11-23T12:00:00Z"
    assert manual["collection_method"] == "send_invoice"
    assert manual["attempted"] is False and manual["attempt_count"] == 0
    assert manual["next_payment_attempt_utc"] is None
    assert result["payment_review"]["open_with_balance_count"] == 2
    assert "not proof of failed payment" in result["note"]
    assert "in_open_attempts" not in str(result) and "cus_owner" not in str(result)
    assert "secret@example.com" not in str(result)


def test_missing_collection_evidence_stays_unknown_not_zero():
    row = history().read(Tenant())["invoices"][0]
    assert row["collection_method"] is None and row["attempted"] is None
    assert row["attempt_count"] is None and row["next_payment_attempt_utc"] is None


def test_payment_review_zero_means_zero_in_verified_page_only():
    result = history(fetch=lambda *_: {"object": "list", "data": [],
                                        "has_more": False}).read(Tenant())
    assert result["verified"] is True
    assert result["payment_review"]["open_with_balance_count"] == 0
    assert result["payment_review"]["uncollectible_count"] == 0
    assert result["payment_review"]["open_balance_by_currency_minor"] == {}
    assert result["payment_review"]["complete_history"] is True
    assert history().read(Tenant(payer_id=None))["payment_review"] is None


def test_open_invoice_with_no_remaining_balance_is_not_payment_attention():
    result = history(fetch=lambda *_: {
        "object": "list", "has_more": False, "data": [
            invoice(status="open", amount_due=0, amount_paid=0, amount_remaining=0)
        ]}).read(Tenant())
    assert result["payment_review"]["open_with_balance_count"] == 0
    assert result["payment_review"]["open_balance_by_currency_minor"] == {}
