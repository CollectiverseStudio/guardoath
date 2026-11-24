"""Owner invoice traversal: actual service/API, encrypted cursors and read audit."""
import base64
import copy
import io
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor
from guardoath.admin_billing import AdminSubscriptionReporter
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.admin_invoices import (
    AdminInvoiceHistory, InvalidInvoiceCursor, InvoiceHistoryUnavailable,
    InvoiceReadRateLimited, fetch_invoice_list,
)
from guardoath.subscription_reconciliation import StripeBinding

OWNER = "12345678-1234-1234-1234-123456789012"
OTHER_OWNER = "87654321-4321-4321-4321-210987654321"
NOW = 1795500000
KEY = b"synthetic-invoice-cursor-key-not-production"


def record(**changes):
    return SimpleNamespace(tenant_id="usr-invoices", payer_id="cus_private",
        stripe_subscription_id="sub_private", demo=False, status="active",
        billing_mode="stripe", subscription_status="active", revision=3, **changes)


def invoices():
    return [{"object":"invoice", "id":f"in_private_{i}", "customer":"cus_private",
        "subscription":"sub_private", "livemode":False, "created":NOW - 86400 * i,
        "status":"open" if i % 3 == 0 else "uncollectible" if i % 3 == 1 else "paid",
        "currency":"eur" if i % 2 else "usd", "amount_due":100 + i,
        "amount_paid":100 + i if i % 3 == 2 else 0,
        "amount_remaining":0 if i % 3 == 2 else 100 + i,
        "customer_email":"private@example.invalid", "hosted_invoice_url":"https://private.invalid"}
        for i in range(12)]


class Provider:
    def __init__(self):
        self.calls = []
        self.rows = invoices()
        self.mutate = lambda value: value

    def subscription(self, tenant, customer, subscription):
        self.calls.append(("subscription", tenant, customer, subscription))
        return {"object":"subscription", "id":subscription, "customer":customer,
            "livemode":False, "status":"active", "collection_method":"charge_automatically",
            "pause_collection":None, "items":{"has_more":False,
                "data":[{"quantity":1, "price":{"id":"price_plan"}}]}}

    def fetch(self, tenant, customer, subscription, *, starting_after=None, status=None):
        self.calls.append(("invoices", tenant, customer, subscription, starting_after, status))
        rows = [copy.deepcopy(row) for row in self.rows if status is None or row["status"] == status]
        if starting_after is not None:
            position = next(i for i, row in enumerate(rows) if row["id"] == starting_after)
            rows = rows[position + 1:]
        return self.mutate({"object":"list", "data":rows[:5], "has_more":len(rows) > 5})


def service(provider=None, *, clock=None, key=KEY, price="price_plan", live=False):
    provider = provider or Provider()
    return AdminInvoiceHistory(provider.fetch,
        AdminSubscriptionReporter(provider.subscription, expected_price_id=price, expected_livemode=live),
        cursor_key=key, clock=clock or (lambda:NOW)), provider


def read(history, *, tenant=None, owner=OWNER, cursor=None, status=None):
    return history.read(tenant or record(), owner_sub=owner, cursor=cursor, status=status)


def test_three_pages_reverify_binding_redact_ids_and_never_accumulate_money():
    history, provider = service()
    saved = record()
    before = copy.deepcopy(vars(saved))
    first = read(history, tenant=saved)
    second = read(history, tenant=saved, cursor=first["next_cursor"])
    last = read(history, tenant=saved, cursor=second["next_cursor"])
    assert [page["page_number"] for page in (first, second, last)] == [1, 2, 3]
    assert [page["count"] for page in (first, second, last)] == [5, 5, 2]
    assert [page["has_more"] for page in (first, second, last)] == [True, True, False]
    assert last["next_cursor"] is None
    assert all(page["payment_review"]["complete_history"] is False for page in (first, second, last))
    assert first["payment_review"]["scope"] == "verified_first_page_only"
    assert second["payment_review"]["scope"] == last["payment_review"]["scope"] == "verified_selected_page_only"
    assert last["payment_review"]["open_with_balance_count"] == 0
    assert [call[0] for call in provider.calls] == ["subscription", "invoices"] * 3
    assert [call[4] for call in provider.calls if call[0] == "invoices"] == [None, "in_private_4", "in_private_9"]
    assert vars(saved) == before
    serialized = json.dumps([first, second, last])
    for secret in ("in_private", "cus_private", "sub_private", "private@example", "https://private"):
        assert secret not in serialized
    cipher = base64.urlsafe_b64decode(first["next_cursor"][4:] + "=" * (-len(first["next_cursor"][4:]) % 4))
    assert b"in_private_4" not in cipher and OWNER.encode() not in cipher


@pytest.mark.parametrize("status", ["open", "uncollectible", "paid", "draft", "void"])
def test_status_filter_remains_page_scoped_even_when_selection_is_exhausted(status):
    history, provider = service()
    result = read(history, status=status)
    assert result["status_filter"] == status
    assert all(row["status"] == status for row in result["invoices"])
    assert result["has_more"] is False and result["next_cursor"] is None
    assert result["payment_review"]["complete_history"] is False
    assert result["payment_review"]["scope"] == "verified_selected_page_only"
    assert provider.calls[-1][-1] == status


@pytest.mark.parametrize("change", ["owner", "tenant", "customer", "subscription", "price", "mode", "key", "filter"])
def test_cursor_cannot_cross_authority_or_selection_and_refuses_before_provider(change):
    history, provider = service()
    cursor = read(history)["next_cursor"]
    provider.calls.clear()
    tenant, owner, status = record(), OWNER, None
    if change == "owner": owner = OTHER_OWNER
    if change == "tenant": tenant.tenant_id = "usr-other"
    if change == "customer": tenant.payer_id = "cus_other"
    if change == "subscription": tenant.stripe_subscription_id = "sub_other"
    if change == "price": history, _ = service(provider, price="price_other")
    if change == "mode": history, _ = service(provider, live=True)
    if change == "key": history, _ = service(provider, key=b"different-private-cursor-secret-0123456789")
    if change == "filter": status = "open"
    with pytest.raises(InvalidInvoiceCursor):
        read(history, tenant=tenant, owner=owner, cursor=cursor, status=status)
    assert provider.calls == []


@pytest.mark.parametrize("cursor", ["", "in_private_4", "ic1.bad", "ic1." + "a" * 2045, 1, False, [], {}])
def test_untrusted_cursor_shapes_never_reach_provider(cursor):
    history, provider = service()
    with pytest.raises(InvalidInvoiceCursor):
        read(history, cursor=cursor)
    assert provider.calls == []


@pytest.mark.parametrize("status", ["", "all", "failed", "OPEN", "open&customer=cus_other", [], {}, False])
def test_nonallowlisted_status_never_reaches_provider(status):
    history, provider = service()
    with pytest.raises(InvalidInvoiceCursor):
        read(history, status=status)
    assert provider.calls == []


def test_tampered_expired_and_future_cursors_fail_before_provider_and_expiry_does_not_slide():
    clock = [NOW]
    history, provider = service(clock=lambda:clock[0])
    first = read(history)["next_cursor"]
    provider.calls.clear()
    tampered = first[:20] + ("a" if first[20] != "a" else "b") + first[21:]
    for bad in (tampered, first + "=", first + ".", "v4." + first[4:]):
        with pytest.raises(InvalidInvoiceCursor): read(history, cursor=bad)
    assert provider.calls == []
    clock[0] -= 1
    with pytest.raises(InvalidInvoiceCursor): read(history, cursor=first)
    assert provider.calls == []
    clock[0] = NOW + 850
    next_cursor = read(history, cursor=first)["next_cursor"]
    provider.calls.clear()
    clock[0] = NOW + 900
    with pytest.raises(InvalidInvoiceCursor): read(history, cursor=next_cursor)
    assert provider.calls == []


def test_unrelated_revision_change_does_not_change_billing_authority():
    history, _ = service()
    cursor = read(history)["next_cursor"]
    tenant = record()
    tenant.revision = 91
    assert read(history, tenant=tenant, cursor=cursor)["page_number"] == 2


def test_filtered_continuation_accepts_equal_creation_times_and_keeps_scope():
    history, provider = service()
    for row in provider.rows:
        row.update(status="open", created=NOW, amount_paid=0, amount_remaining=row["amount_due"])
    first = read(history, status="open")
    second = read(history, status="open", cursor=first["next_cursor"])
    last = read(history, status="open", cursor=second["next_cursor"])
    assert [item["count"] for item in (first, second, last)] == [5, 5, 2]
    assert [item["has_more"] for item in (first, second, last)] == [True, True, False]
    assert all(item["status_filter"] == "open" and item["payment_review"]["complete_history"] is False
               and item["payment_review"]["scope"] == "verified_selected_page_only" for item in (first, second, last))
    assert [call[4:] for call in provider.calls if call[0] == "invoices"] == [
        (None,"open"), ("in_private_4","open"), ("in_private_9","open")]


@pytest.mark.parametrize("fault", ["empty_more", "anchor", "newer", "unsorted", "other_customer", "duplicate", "wrong_filter"])
def test_ambiguous_or_nonadvancing_provider_page_is_not_accepted(fault):
    history, provider = service()
    cursor = read(history)["next_cursor"]
    def corrupt(value):
        if fault == "empty_more": value.update(data=[], has_more=True)
        elif fault == "anchor": value["data"][0]["id"] = "in_private_4"
        elif fault == "newer": value["data"][0]["created"] = NOW
        elif fault == "unsorted": value["data"] = list(reversed(value["data"]))
        elif fault == "other_customer": value["data"][0]["customer"] = "cus_other"
        elif fault == "duplicate": value["data"][1]["id"] = value["data"][0]["id"]
        elif fault == "wrong_filter": value["data"][0]["status"] = "paid"
        return value
    provider.mutate = corrupt
    with pytest.raises(InvoiceHistoryUnavailable):
        read(history, cursor=None if fault == "wrong_filter" else cursor,
             status="open" if fault == "wrong_filter" else None)


def test_provider_reverification_failure_prevents_continuation_invoice_get():
    history, provider = service()
    cursor = read(history)["next_cursor"]
    provider.calls.clear()
    history.reporter.fetch = lambda *_: (_ for _ in ()).throw(RuntimeError("private provider detail"))
    with pytest.raises(InvoiceHistoryUnavailable): read(history, cursor=cursor)
    assert provider.calls == []


def test_saved_binding_is_immutable_during_subscription_verification():
    history, provider = service()
    saved = record()
    fetch = history.reporter.fetch
    def mutate_saved(*args):
        saved.payer_id = "cus_changed_during_read"
        return fetch(*args)
    history.reporter.fetch = mutate_saved
    assert read(history, tenant=saved)["verified"] is True
    assert provider.calls[-1][2] == "cus_private"


def test_provider_budget_bounds_repeated_pages_and_recovers_after_window():
    clock = [NOW]
    history, provider = service(clock=lambda:clock[0])
    for _ in range(20): read(history)
    assert len(provider.calls) == 40
    with pytest.raises(InvoiceReadRateLimited): read(history)
    assert len(provider.calls) == 40
    clock[0] += 60
    assert read(history)["verified"] is True
    assert len(provider.calls) == 42


@pytest.mark.parametrize("now", [True, None, "now", float("nan"), float("inf"), 0])
def test_invalid_clock_never_calls_provider(now):
    history, provider = service(clock=lambda:now)
    with pytest.raises(InvoiceHistoryUnavailable): read(history)
    assert provider.calls == []


def test_transport_sends_only_fixed_get_filters_and_preserves_pinned_api():
    captured = []
    class Response(io.BytesIO): status = 200
    def opener(request, *, timeout):
        captured.append(request)
        assert timeout == 5.0
        return Response(b'{"object":"list","data":[],"has_more":false}')
    fetch_invoice_list(StripeBinding("usr-invoices", "cus_private", "sub_private"),
        "sk_test_" + "x" * 24, opener=opener, starting_after="in_private_4", status="open")
    request = captured[0]
    assert request.get_method() == "GET" and request.data is None
    assert urlsplit(request.full_url).netloc == "api.stripe.com"
    assert parse_qs(urlsplit(request.full_url).query) == {"customer":["cus_private"],
        "subscription":["sub_private"], "limit":["5"], "starting_after":["in_private_4"], "status":["open"]}
    assert request.get_header("Stripe-version") == "2025-02-24.acacia"


def test_actual_api_audits_each_page_and_rejects_cross_owner_before_provider(tmp_path):
    history, provider = service()
    db = AdminTestDynamo(tmp_path / "invoice-read-audit.sqlite")
    auditor = AdminSensitiveReadAuditor(db, "audit_table", signing_key=ADMIN_BROWSER_AUDIT_KEY)
    reader = AdminAuditReader(db, "audit_table", verify_key=ADMIN_BROWSER_AUDIT_KEY)
    stored = record()
    class Store:
        def get(self, tenant_id): return stored if tenant_id == stored.tenant_id else None
    auth = lambda token: VerifiedAdmin(OWNER if token == "owner" else OTHER_OWNER,
        frozenset({"owner"}) if token in {"owner", "other", "no-mfa"} else frozenset({"customer"}),
        token != "no-mfa")
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=auth, tenant_store=Store(),
        billing_invoices=history, read_auditor=auditor, require_persistent_read_audit=True))
    client = TestClient(app)
    url = "/v1/admin/subscriptions/usr-invoices/invoices"
    for headers in ({}, {"X-GuardOath-Key":"owner"}, {"X-GuardOath-Admin-Session":"customer"},
                    {"X-GuardOath-Admin-Session":"no-mfa"}):
        assert client.get(url, headers=headers).status_code in (401, 403)
    assert provider.calls == [] and db.audit_items() == []
    first = client.get(url, headers={"X-GuardOath-Admin-Session":"owner"})
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    cursor = first.json()["history"]["next_cursor"]
    calls = list(provider.calls)
    denied = client.get(url, params={"cursor":cursor}, headers={"X-GuardOath-Admin-Session":"other"})
    assert denied.status_code == 400 and provider.calls == calls
    second = client.get(url, params={"cursor":cursor}, headers={"X-GuardOath-Admin-Session":"owner"})
    assert second.status_code == 200 and second.json()["history"]["page_number"] == 2
    receipts = [reader.read(item["event_id"]["S"]) for item in db.audit_items()]
    assert len(receipts) == 3
    assert all(receipt["integrity_status"] == "signed_verified" and
        receipt["change"] == {"field":"protected_read", "resource":"stripe.invoices"}
        and receipt["tenant_id"] == "usr-invoices" for receipt in receipts)
    assert first.json()["read_audit_event_id"] != second.json()["read_audit_event_id"]
    db.close()


def test_audit_outage_prevents_tenant_read_and_any_provider_page():
    history, provider = service()
    class Audit:
        def log(self, **_): raise RuntimeError("private audit outage")
    class Store:
        def get(self, *_): pytest.fail("Tenant must not be read before durable audit")
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=lambda _:VerifiedAdmin(OWNER,frozenset({"owner"}),True),
        tenant_store=Store(), billing_invoices=history, read_auditor=Audit(), require_persistent_read_audit=True))
    response = TestClient(app).get("/v1/admin/subscriptions/usr-invoices/invoices",
        headers={"X-GuardOath-Admin-Session":"owner"}, params={"cursor":"ic1." + "a" * 80})
    assert response.status_code == 503
    assert "private" not in response.text and provider.calls == []
