"""Actual owner refund routes, strict authority and independently signed recovery."""
import copy
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from admin_refund_support import ACTOR, OTHER, TENANT, KEY, invoice_reference, request, review, service
from admin_test_support import AdminTestDynamo
from guardoath import state_contracts
from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.admin_invoice_refund import AdminInvoiceRefund

PREFIX = "/v1/admin/subscriptions/" + TENANT
HEADERS = {"X-GuardOath-Admin-Session":"owner", "Origin":"https://guardoath.com"}
ENDPOINTS = ("/refund/preview", "/refund", "/refund/receipt", "/refund/refresh")


def client(actions, db, verifier=None):
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=verifier or (
        lambda token:VerifiedAdmin(ACTOR, frozenset({"owner"}), True) if token == "owner" else None),
        billing_refunds=actions,
        read_auditor=AdminSensitiveReadAuditor(db, "audit_table", signing_key=KEY),
        require_persistent_read_audit=True))
    return TestClient(app)


def payload(endpoint):
    if endpoint == "/refund/preview":
        return {"expected_revision":3, "invoice_reference":"ir1." + "a" * 80}
    value = {"idempotency_key":"refund-reference-123456789"}
    if endpoint == "/refund":
        value.update(expected_revision=3, preview_reference="rf1." + "a" * 80,
            confirmation="REFUND " + TENANT + " 1299 USD MINOR UNITS",
            reason="Review the full synthetic invoice refund")
    return value


def submitted(reviewed):
    return {key:value for key,value in request(reviewed).items() if key not in ("actor", "tenant_id")}


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", True)


def test_exact_invoice_review_dispatch_and_signed_history_preserve_tenant(enabled):
    actions, db, boundary = service()
    browser = client(actions, db)
    before = copy.deepcopy(db.get_item(TableName="tenant_table", Key={"tenant_id":{"S":TENANT}}))
    first = browser.post(PREFIX + "/refund/preview", headers=HEADERS,
        json={"expected_revision":3, "invoice_reference":invoice_reference(actions)})
    assert first.status_code == 200, first.text
    preview = first.json()["refund"]
    assert preview["confirmation"] == "REFUND " + TENANT + " 1299 USD MINOR UNITS"
    assert preview["currency_exponent"] == 2 and preview["livemode"] is False
    body = submitted(preview)
    second = browser.post(PREFIX + "/refund", headers=HEADERS, json=body)
    assert second.status_code == 200 and second.json()["refund"]["status"] == "verified", second.text
    read_count = len(boundary.calls)
    third = browser.post(PREFIX + "/refund/receipt", headers=HEADERS,
        json={"idempotency_key":body["idempotency_key"]})
    assert third.status_code == 200 and third.json()["refund"] == second.json()["refund"]
    assert len(boundary.calls) == read_count
    repeated = browser.post(PREFIX + "/refund", headers=HEADERS, json=body)
    assert repeated.status_code == 200 and repeated.json()["refund"] == second.json()["refund"]
    assert len(boundary.calls) == read_count and len(boundary.posts) == 1
    reader = AdminAuditReader(db, "audit_table", verify_key=KEY)
    for response, resource in ((first,"stripe.invoices"), (second,"stripe.invoices"),
                               (third,"audit.receipt"), (repeated,"stripe.invoices")):
        assert response.headers["cache-control"] == "no-store"
        audit = reader.read(response.json()["read_audit_event_id"])
        assert audit["integrity_status"] == "signed_verified"
        assert audit["change"] == {"field":"protected_read", "resource":resource}
        assert all(secret not in response.text for secret in
            ("cus_refund", "sub_refund", "in_refund", "ch_refund", "pi_refund", "re_refund"))
    assert db.get_item(TableName="tenant_table", Key={"tenant_id":{"S":TENANT}}) == before


def test_pending_requires_explicit_provider_read_and_retains_permanent_claim(enabled):
    actions, db, boundary = service()
    boundary.status = "pending"
    browser = client(actions, db)
    body = submitted(review(actions))
    result = browser.post(PREFIX + "/refund", headers=HEADERS, json=body)
    assert result.status_code == 200 and result.json()["refund"]["status"] == "pending"
    boundary.status = "succeeded"
    count = len(boundary.calls)
    reference = {"idempotency_key":body["idempotency_key"]}
    history = browser.post(PREFIX + "/refund/receipt", headers=HEADERS, json=reference)
    assert history.json()["refund"]["status"] == "pending" and len(boundary.calls) == count
    refreshed = browser.post(PREFIX + "/refund/refresh", headers=HEADERS, json=reference)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refund"]["status"] == "verified"
    assert refreshed.json()["refund"]["provider_state_refreshed"] is True
    assert all(call.get_method() == "GET" for call in boundary.calls[count:])
    assert len(boundary.posts) == 1
    replay = browser.post(PREFIX + "/refund", headers=HEADERS, json=body)
    assert replay.status_code == 200 and replay.json()["refund"]["status"] == "verified"
    competing = browser.post(PREFIX + "/refund", headers=HEADERS,
        json={**body, "idempotency_key":"different-refund-reference-123"})
    assert competing.status_code == 409 and len(boundary.posts) == 1


def test_source_disabled_reader_reopens_history_without_provider_or_tenant(enabled, monkeypatch):
    actions, db, boundary = service()
    body = submitted(review(actions))
    expected = actions.apply(actor=ACTOR, tenant_id=TENANT, **body)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", False)
    reader_only = AdminInvoiceRefund(state=actions.state, tenant_table="tenant_table", preview_key=KEY)
    browser = client(reader_only, db)
    calls = len(boundary.calls)
    original = db.get_item
    def audited_only(**kwargs):
        assert kwargs["TableName"] == "audit_table"
        return original(**kwargs)
    monkeypatch.setattr(db, "get_item", audited_only)
    recovered = browser.post(PREFIX + "/refund/receipt", headers=HEADERS,
        json={"idempotency_key":body["idempotency_key"]})
    assert recovered.status_code == 200 and recovered.json()["refund"] == expected
    assert len(boundary.calls) == calls
    flags = browser.get("/v1/admin/features", headers=HEADERS).json()
    assert flags["billing_refund_receipts"] is True
    assert all(flags[name] is False for name in
        ("billing_refund_preview", "billing_refund_submit", "billing_refund_refresh"))
    for endpoint in ("/refund", "/refund/refresh"):
        assert browser.post(PREFIX + endpoint, headers=HEADERS, json=payload(endpoint)).status_code == 404


def test_source_disabled_keeps_authoritative_review_but_has_no_write_routes(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", False)
    actions, db, boundary = service()
    browser = client(actions, db)
    result = browser.post(PREFIX + "/refund/preview", headers=HEADERS,
        json={"expected_revision":3, "invoice_reference":invoice_reference(actions)})
    assert result.status_code == 200 and result.json()["refund"]["refund_available"] is False
    for endpoint in ("/refund", "/refund/refresh"):
        assert browser.post(PREFIX + endpoint, headers=HEADERS, json=payload(endpoint)).status_code == 404
    assert not boundary.posts and all("financial_operation" not in row for row in db.audit_items())


@pytest.mark.parametrize("role,mfa", [("customer",True), ("billing_admin",True), ("support",True), ("owner",False)])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_non_owner_or_missing_mfa_denied_before_any_boundary(enabled, role, mfa, endpoint):
    actions, db, boundary = service()
    browser = client(actions, db, lambda _:VerifiedAdmin(ACTOR, frozenset({role}), mfa))
    result = browser.post(PREFIX + endpoint, headers=HEADERS, json=payload(endpoint))
    assert result.status_code == 403 and not boundary.calls and not db.audit_items()


@pytest.mark.parametrize("headers", [{}, {"X-GuardOath-Key":"customer-key"},
    {"X-GuardOath-Admin-Session":"forged"}])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_unverified_identity_is_not_refund_authority(enabled, headers, endpoint):
    actions, db, boundary = service()
    result = client(actions, db).post(PREFIX + endpoint,
        headers={**headers, "Origin":HEADERS["Origin"]}, json=payload(endpoint))
    assert result.status_code == 401 and not boundary.calls and not db.audit_items()


@pytest.mark.parametrize("origin", [None, "https://foreign.invalid", "http://localhost:8780"])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_every_refund_post_requires_same_origin(enabled, origin, endpoint):
    actions, db, boundary = service()
    headers = {key:value for key,value in HEADERS.items() if key != "Origin"}
    if origin is not None:
        headers["Origin"] = origin
    response = client(actions, db).post(PREFIX + endpoint, headers=headers, json=payload(endpoint))
    assert response.status_code == 403 and not boundary.calls and not db.audit_items()


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("extra", [{"amount_minor":1299}, {"currency":"usd"}, {"invoice_id":"in_other"},
    {"charge_id":"ch_other"}, {"execution_budget_seconds":8}])
def test_no_browser_authority_for_amount_provider_ids_or_time_budget(enabled, endpoint, extra):
    actions, db, boundary = service()
    response = client(actions, db).post(PREFIX + endpoint, headers=HEADERS,
        json={**payload(endpoint), **extra})
    assert response.status_code == 422 and not boundary.calls and not db.audit_items()


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_audit_outage_precedes_every_protected_refund_read(enabled, monkeypatch, endpoint):
    actions, db, boundary = service()
    db.fail_audit = True
    reads = []
    monkeypatch.setattr(db, "get_item", lambda **kwargs:reads.append(kwargs))
    result = client(actions, db).post(PREFIX + endpoint, headers=HEADERS, json=payload(endpoint))
    assert result.status_code == 503 and not reads and not boundary.calls


def test_wrong_owner_invoice_selection_and_confirmation_never_dispatch(enabled):
    actions, db, boundary = service()
    wrong = invoice_reference(actions, actor=OTHER)
    browser = client(actions, db)
    selected = browser.post(PREFIX + "/refund/preview", headers=HEADERS,
        json={"expected_revision":3, "invoice_reference":wrong})
    assert selected.status_code == 409 and not boundary.calls
    body = submitted(review(actions))
    count = len(boundary.calls)
    denied = browser.post(PREFIX + "/refund", headers=HEADERS,
        json={**body, "confirmation":"REFUND " + TENANT + " 1298 USD MINOR UNITS"})
    assert denied.status_code == 409 and len(boundary.calls) == count
    assert all("financial_operation" not in row for row in db.audit_items())


def test_provider_failure_has_private_safe_error(enabled):
    actions, db, boundary = service()
    boundary.before = lambda _:(_ for _ in ()).throw(RuntimeError("rk_live_private cus_private provider body"))
    result = client(actions, db).post(PREFIX + "/refund/preview", headers=HEADERS,
        json={"expected_revision":3, "invoice_reference":invoice_reference(actions)})
    assert result.status_code == 503 and "private" not in result.text and "rk_" not in result.text


@pytest.mark.parametrize("remaining,expected", [(15000,200), (3000,503), (True,503)])
def test_submit_budget_comes_only_from_trusted_lambda_context(enabled, remaining, expected):
    actions, db, boundary = service()
    body = submitted(review(actions))
    browser = client(actions, db)
    @browser.app.middleware("http")
    async def context(request, call_next):
        request.scope["aws.context"] = SimpleNamespace(get_remaining_time_in_millis=lambda:remaining)
        return await call_next(request)
    result = browser.post(PREFIX + "/refund", headers=HEADERS, json=body)
    assert result.status_code == expected
    assert len(boundary.posts) == (1 if expected == 200 else 0)


@pytest.mark.parametrize("mismatch", ["client", "table", "key"])
def test_refund_router_requires_same_persistent_audit_authority(enabled, mismatch):
    actions, db, _ = service()
    auditor = AdminSensitiveReadAuditor(AdminTestDynamo() if mismatch == "client" else db,
        "other_audit" if mismatch == "table" else "audit_table",
        signing_key=b"other-refund-audit-key-01234567890123456789" if mismatch == "key" else KEY)
    with pytest.raises(ValueError, match="bound persistent"):
        create_admin_router(billing_refunds=actions, read_auditor=auditor)
