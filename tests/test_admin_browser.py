"""Prove browser PKCE bootstrap does not disclose a token without provider-verified owner+MFA."""
import json
import re
from urllib.parse import parse_qs

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from guardoath.admin_browser import (BrowserAuthConfig, BoundExchange,
                                     ExchangeUnavailable, create_browser_router)
from guardoath.admin_console import VerifiedAdmin


CFG = BrowserAuthConfig("acme.auth.us-east-1.amazoncognito.com", "testclient123",
                        "https://guardoath.com/admin/login")


def owner(token):
    if token == "a" * 40:
        return VerifiedAdmin("subject", frozenset({"owner"}), True)
    return None


def app(sender=None, checker=owner):
    application = FastAPI()
    exchange = BoundExchange(CFG, sender=sender or (lambda url, body: {"access_token": "a" * 40}))
    application.include_router(create_browser_router(CFG, exchange, checker))
    return TestClient(application)


def payload(**kwargs):
    return {"code": "code1", "code_verifier": "x" * 43, **kwargs}


def test_catalog_draft_browser_workflow_is_authenticated_and_never_publishes():
    page = app().get("/admin/login").text
    script = app().get("/admin/client.js").text
    assert 'id="proposal-save"' in page
    assert 'id="draft-id"' in page and 'id="draft-inspect"' in page
    assert 'features.catalog_drafts' in script
    assert 'request("/v1/admin/catalog/drafts", {method:"POST"' in script
    assert 'request("/v1/admin/catalog/drafts/" + id)' in script
    assert 'result.draft.published !== false' in script
    assert 'ready_to_publish !== false' in script
    assert 'previewReady = null;' in script
    assert '"catalog.draft"' in script


def test_catalog_review_ui_is_owner_session_bound_and_receipt_verified():
    page = app().get("/admin/login").text
    script = app().get("/admin/client.js").text
    assert 'id="draft-review"' in page
    assert 'type="button" disabled>Record signed review' in page
    assert "draftReadyForReview = null;" in script
    assert "features.catalog_review_receipts" in script
    assert "if (!result.stale && features.catalog_review_receipts" in script
    assert '"REVIEW " + selected.id.slice(-8)' in script
    assert 'request("/v1/admin/catalog/drafts/" + selected.id + "/review", {' in script
    assert 'request("/v1/admin/catalog/reviews/" + result.review_id)' in script
    assert 'result.reviewed !== true' in script
    assert 'result.published !== false' in script
    assert 'verified.review.published !== false' in script
    assert 'verified.ready_to_publish !== false' in script
    assert 'typeof verified.stale !== "boolean"' in script
    assert 'verified.source_current !== !verified.stale' in script
    assert "review cannot be used as current-source evidence" in script
    assert "el(\"draft-review\").disabled = true;" in script
    assert 'addEventListener("click", reviewCatalogDraft)' in script
    assert "NOT publish rules" in script or "NOT publish" in script
    assert 'sessionStorage.setItem("access_token"' not in script


def test_login_shell_is_public_but_contains_no_sensitive_information():
    client = app()
    page = client.get("/admin/login")
    assert page.status_code == 200
    assert "GuardOath Administration" in page.text
    assert "client.js" in page.text
    assert "access_token" not in page.text
    assert "default-src 'none'" in page.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert page.headers["Cache-Control"] == "no-store"
    script = client.get("/admin/client.js")
    assert script.status_code == 200
    assert "sessionStorage.setItem(\"go_admin_pkce\"" in script.text
    assert 'sessionStorage.setItem("access_token"' not in script.text
    assert "location.assign(uri.href)" in script.text
    assert "textContent =" in script.text  # no untrusted HTML insertion
    assert "innerHTML" not in script.text
    assert 'headers["X-GuardOath-Admin-Session"] = token' in script.text
    assert 'headers.Authorization = "Bearer " + token' not in script.text


def test_public_config_contains_only_oauth_metadata_and_native_capability():
    calls = []
    result = app(sender=lambda *_: calls.append("exchange"),
                 checker=lambda _: calls.append("verify")).get("/v1/admin/auth/config")
    assert result.status_code == 200
    assert result.json() == {"domain": CFG.domain, "client_id": CFG.client_id,
                             "redirect_uri": CFG.redirect_uri,
                             "native_mfa_enabled": False}
    assert result.headers["Cache-Control"] == "no-store"
    # A public capability read cannot authenticate or disclose private session state.
    assert calls == []
    assert "access_token" not in result.text
    assert "challenge_ticket" not in result.text


def test_exchange_uses_only_fixed_server_url_and_redirect():
    called = []
    def sender(url, body):
        called.append((url, parse_qs(body.decode())))
        return {"access_token": "a" * 40}
    result = app(sender).post("/v1/admin/auth/exchange", json=payload())
    assert result.status_code == 200
    assert result.json() == {"access_token": "a" * 40, "token_type": "Bearer"}
    assert result.headers["Cache-Control"] == "no-store"
    assert result.headers["Pragma"] == "no-cache"
    assert called == [("https://" + CFG.domain + "/oauth2/token", {
        "grant_type": ["authorization_code"], "client_id": [CFG.client_id],
        "redirect_uri": [CFG.redirect_uri], "code": ["code1"], "code_verifier": ["x" * 43]})]


@pytest.mark.parametrize("body", [
    {"code": "has spaces", "code_verifier": "x" * 43},
    {"code": "valid", "code_verifier": "x" * 42},
    {"code": "valid", "code_verifier": "ü" * 43},
    {"code": "valid", "code_verifier": "x" * 43, "redirect_uri": "https://evil.invalid"},
])
def test_invalid_or_untrusted_exchange_requests_never_call_provider(body):
    calls = []
    client = app(sender=lambda url, form: calls.append(1))
    result = client.post("/v1/admin/auth/exchange", json=body)
    assert result.status_code in {401, 422}
    assert calls == []


@pytest.mark.parametrize("verified", [None, VerifiedAdmin("subject", frozenset({"user"}), True),
                                    VerifiedAdmin("subject", frozenset({"owner"}), False)])
def test_no_token_disclosed_to_non_owner_or_missing_mfa(verified):
    result = app(checker=lambda _: verified).post("/v1/admin/auth/exchange", json=payload())
    assert result.status_code == 403
    assert "access_token" not in result.text


def test_provider_failures_refuse_without_leaking_token():
    def failure(url, body):
        raise ExchangeUnavailable("do not display this")
    result = app(sender=failure).post("/v1/admin/auth/exchange", json=payload())
    assert result.status_code == 503
    assert "do not display" not in result.text
    assert "access_token" not in result.text


def test_verifier_failure_is_503_and_does_not_leak_token():
    def failure(token):
        raise RuntimeError("private provider details")
    result = app(checker=failure).post("/v1/admin/auth/exchange", json=payload())
    assert result.status_code == 503
    assert "private provider details" not in result.text


@pytest.mark.parametrize("kwargs", [
    {"domain": "evil.com"},
    {"domain": "acme.auth.eu-west-1.amazoncognito.com.attacker.invalid"},
    {"client_id": "client id"},
    {"redirect_uri": "https://evil.invalid/admin/login"},
    {"redirect_uri": "http://guardoath.com/admin/login"},
])
def test_config_refuses_untrusted_provider_and_redirect(kwargs):
    with pytest.raises(ValueError):
        BrowserAuthConfig(**{**CFG.__dict__, **kwargs})


def test_rate_limit_rejects_after_thirty_per_minute():
    exchanger = BoundExchange(CFG, sender=lambda url, body: {"access_token": "a" * 40}, clock=lambda: 100.0)
    for _ in range(30):
        assert exchanger.exchange("code", "x" * 43) == "a" * 40
    with pytest.raises(ExchangeUnavailable):
        exchanger.exchange("code", "x" * 43)


def test_cognito_user_table_and_owner_only_browser_request():
    page = app().get("/admin/login")
    assert 'id="user-rows"' in page.text
    assert "<th>API keys</th>" in page.text
    assert '"api_key_count"' in app().get("/admin/client.js").text
    assert 'id="user-search-go"' in page.text
    assert 'id="user-next"' in page.text
    script = app().get("/admin/client.js").text
    assert 'request("/v1/admin/users?limit=20")' in script
    assert "showUsers(users)" in script
    assert 'headers["X-GuardOath-Admin-Session"] = token' in script
    assert "email_hint" in script and "user-rows" in script
    assert "innerHTML" not in script


def test_owner_mutation_ui_requires_consistent_atomic_audit_receipt():
    script = app().get("/admin/client.js").text
    assert "function auditReceipt(result, tenantId, priorRevision)" in script
    assert 'result.tenant_id !== tenantId' in script
    assert 'result.revision !== priorRevision + 1' in script
    assert '/^[a-f0-9]{64}$/.test(result.audit_event_id)' in script
    assert "Administrative change has no verifiable audit receipt" in script
    # Every owner mutation must verify the same atomic audit receipt, including
    # full application-key revocation. Do not weaken the shared validator.
    assert script.count("const receipt = auditReceipt(outcome,") == 4
    for endpoint in ("/status", "/rule-action", "/allowance-cap", "/revoke-keys"):
        assert endpoint in script
    assert 'say("status","Cost cap saved · " + receipt)' in script
    assert 'say("status","Tenant rule saved · " + receipt)' in script
    assert 'say("status", "Tenant access updated · " + receipt)' in script
    assert "innerHTML" not in script


def test_admin_browser_disables_cap_action_when_allowance_is_unknown():
    script = app().get("/admin/client.js").text
    assert "Number.isSafeInteger(tenant.monthly_allowance) && tenant.monthly_allowance >= 0" in script
    assert "if (!Number.isSafeInteger(tenant.monthly_allowance) || tenant.monthly_allowance < 0) return;" in script
    assert "innerHTML" not in script

def cleared_on_logout(script):
    """Confirm logout invokes the full private-status reset; order is not significant."""
    clear = script.split("  const clearAdminData = () => {", 1)[1].split("  const signOut =", 1)[0]
    signout = script.split("  const signOut =", 1)[1].split("  async function load()", 1)[0]
    assert "clearAdminData();" in signout
    match = re.search(r'for \(const id of \[([^\]]+)\]\) say\(id, ""\);', clear)
    assert match is not None, "Private statuses must be reset to empty strings"
    return set(json.loads("[" + match.group(1) + "]"))


def test_owner_browser_audit_lookup_uses_authenticated_request_and_clears_after_logout():
    shell = app().get("/admin/login").text
    script = app().get("/admin/client.js").text
    assert 'id="audit-id"' in shell and 'id="audit-result"' in shell
    assert 'id="audit-go"' in shell
    assert "features.audit_receipt_lookup" in script
    assert 'request("/v1/admin/audit/" + encodeURIComponent(event))' in script
    assert 'el("audit-go").addEventListener("click", inspectAudit)' in script
    assert "audit-result" in cleared_on_logout(script)
    assert 'el("audit-id").value = ""' in script
    assert "innerHTML" not in script


def test_subscription_reconciliation_diagnostics_visible_only_in_authenticated_owner_browser():
    shell = app().get("/admin/login").text
    script = app().get("/admin/client.js").text
    assert 'id="billing-details"' in shell
    assert "Subscription reconciliation review" in shell
    assert 'request("/v1/admin/subscriptions/" + encodeURIComponent(tenant.tenant_id))' in script
    assert "state.reconciliation || {}" in script
    assert '["critical","review","consistent","unavailable"]' in script
    assert 'say("billing-details", tenant.tenant_id' in script
    assert "no entitlement change" in script
    assert {"billing-details", "audit-result"} <= cleared_on_logout(script)
    assert "innerHTML" not in script

def test_owner_browser_can_inspect_exact_identity_without_url_pii_or_mutating_accounts():
    shell = app().get("/admin/login").text
    script = app().get("/admin/client.js").text
    for marker in ('id="user-exact-email"', 'id="user-inspect"', 'id="user-inspect-result"'):
        assert marker in shell
    assert 'request("/v1/admin/users/lookup", {' in script
    assert 'method: "POST", body: JSON.stringify({email})' in script
    assert 'el("user-exact-email").value = "";' in script
    assert 'el("user-inspect").addEventListener("click", inspectUser)' in script
    # Both existing account inspection and new status management must be wiped on sign-out.
    assert '"user-inspect-result", "user-change-result", "catalog-summary"' in script
    assert 'user-inspect-result' in script
    assert 'innerHTML' not in script
    assert 'admin_disable_user' not in script and 'admin_enable_user' not in script

def test_usage_quota_advisory_is_visible_only_in_admin_authenticated_browser():
    shell = app().get("/admin/login").text
    script = app().get("/admin/client.js").text
    assert 'id="usage-details"' in shell
    assert 'request("/v1/admin/tenants/" + encodeURIComponent(tenant.tenant_id) + "/usage")' in script
    assert "usage.usage_threshold || {}" in script
    assert 'signal.utilization_percent_floor' in script
    assert '" · Recorded quota signal: " + level' in script
    assert '" · Saved-cap behavior: " + policy' in script
    assert '" · AWS costs: Unavailable · Stripe invoices: Unavailable' in script
    assert '"usage-details"' in script.split("const clearAdminData =", 1)[1].split("const signOut", 1)[0]
    assert "innerHTML" not in script

def test_audit_browser_distinguishes_signed_integrity_from_legacy_receipts():
    script = app().get("/admin/client.js").text
    assert 'receipt.integrity_status === "signed_verified"' in script
    assert 'receipt.integrity_status === "legacy_unverified"' in script
    assert "Legacy receipt — integrity unverified" in script
    assert "Audit integrity status unavailable" in script
    assert "innerHTML" not in script


def test_catalog_proposal_is_structured_owner_only_preview_without_publishing():
    shell = app().get("/admin/login").text
    script = app().get("/admin/client.js").text
    assert 'id="proposal-title"' in shell and 'id="proposal-use-case"' in shell
    assert 'id="proposal-go"' in shell and 'id="proposal-result"' in shell
    assert 'request("/v1/admin/catalog/preview", {method:"POST"' in script
    assert 'preview.textContent = "Preview description"' in script
    assert 'result.published !== false' in script and 'result.persisted !== false' in script
    assert 'result.tenant_policies_changed !== false' in script
    assert 'result.fingerprint_kind !== "review_only_untrusted_sha256_not_signature"' in script
    assert '/^[a-f0-9]{64}$/.test(result.baseline_sha256)' in script
    assert '/^[a-f0-9]{64}$/.test(result.proposal_sha256)' in script
    assert "Published baseline SHA-256: " in script
    assert "Proposal SHA-256: " in script
    assert "NOT signatures or approval" in script
    assert "Review fingerprints are not signatures or approvals" in shell
    assert 'el("proposal-go").disabled = true' in script
    assert 'previewCatalogRule);' in script
    assert {"proposal-result", "billing-details", "audit-result"} <= cleared_on_logout(script)


def test_owner_browser_shows_verified_stripe_period_end_and_cancellation_without_invoice_claim():
    script = app().get("/admin/client.js").text
    assert 'state.verified && typeof state.current_period_end_utc === "string"' in script
    assert 'state.verified && state.cancel_at_period_end === true' in script
    assert 'state.verified && state.cancel_at_period_end === false' in script
    assert "Current period ends (UTC): " in script
    assert "Next invoice amount: Unavailable" in script
    assert "innerHTML" not in script


def test_browser_cognito_rate_limit_clears_stale_directory_rows_and_pagination():
    script = app().get("/admin/client.js").text
    assert 'result.status === 429' in script
    assert 'Cognito directory request limit reached (429)' in script
    start = script.index("async function pageUsers()")
    end = script.index("async function inspectUser()", start)
    pager = script[start:end]
    assert 'el("user-rows").replaceChildren();' in pager
    assert 'nextUserCursor = null;' in pager
    assert 'el("user-next").disabled = true;' in pager
    assert 'say("user-status", err.message);' in pager

def test_owner_browser_key_revocation_uses_signed_receipt_and_tenant_confirmation():
    client = app()
    script = client.get("/admin/client.js").text
    assert "Revoke keys" in script
    assert "features.tenant_key_revocation" in script
    assert "tenant.api_key_count > 0" in script
    assert 'encodeURIComponent(tenant.tenant_id) + "/revoke-keys"' in script
    assert "auditReceipt(outcome, tenant.tenant_id, tenant.revision)" in script
    assert "Customers must issue new keys" in script
    assert "Application keys revoked" in script


def test_user_directory_page_displays_signed_read_receipt_when_present():
    shell = app().get("/admin/login").text
    script = app().get("/admin/client.js").text
    assert 'id="user-status"' in shell
    assert '" · total unavailable" + readAuditLabel(data)' in script
    assert "read_audit_event_id" in script
    assert "user-status" in cleared_on_logout(script)
    assert "innerHTML" not in script

def test_cognito_sign_in_status_control_has_server_verified_audit_and_owner_session():
    page = app().get("/admin/login").text
    script = app().get("/admin/client.js").text
    assert 'id="user-change-email"' in page and 'id="user-change-action"' in page
    assert 'id="user-change"' in page and 'id="user-change-result"' in page
    assert 'features.cognito_user_status_changes' in script
    assert 'request("/v1/admin/users/lookup"' in script
    assert 'request("/v1/admin/users/status"' in script
    assert 'expected_enabled:expected' in script
    assert 'receipt.integrity_status !== "signed_verified"' in script
    assert 'receipt.operation !== "cognito.user.status.outcome"' in script
    assert 'change.related_event_id !== result.intent_event_id' in script
    assert 'change.target_fingerprint !== result.target_fingerprint' in script
    assert 'addEventListener("click", changeUserStatus)' in script
    assert 'el("user-change-email").value = ""' in script
    assert 'sessionStorage.setItem("access_token"' not in script


def test_verified_invoice_history_is_an_authenticated_bounded_owner_browser_action():
    shell = app().get("/admin/login").text
    script = app().get("/admin/client.js").text
    assert 'id="invoice-details"' in shell
    assert "Verified recent invoices" in shell
    assert "features.billing_invoices" in script
    assert 'invoices.addEventListener("click", () => inspectInvoices(tenant))' in script
    assert 'encodeURIComponent(tenant.tenant_id) + "/invoices"' in script
    assert "history.verified !== true" in script
    assert "history.invoices.length > 5" in script
    assert "Number.isSafeInteger(n)" in script
    assert "row.amount_paid_minor" in script
    assert "readAuditLabel(result)" in script
    assert "invoice-details" in cleared_on_logout(script)
    assert "innerHTML" not in script


def test_catalog_manifest_browser_requires_signed_fresh_review_and_never_calls_publish():
    shell = app().get("/admin/login").text
    script = app().get("/admin/client.js").text
    assert 'id="review-manifest"' in shell and 'id="review-manifest-result"' in shell
    assert 'el("review-manifest").disabled = true;' in script
    assert 'reviewReadyForManifest = {reviewId:result.review_id, draftId:selected.id,' in script
    assert 'selected.reviewId + "/manifest"' in script
    assert 'result.status !== "metadata_manifest_preview_only"' in script
    assert 'result.ready_to_publish !== false' in script
    assert 'result.acceptance_tests_run !== false' in script
    assert 'result.rollback_source_sha256 !== selected.baseline' in script
    assert "review-manifest-result" in cleared_on_logout(script)
    assert '"NOT published or executable' in script
    assert "innerHTML" not in script


def test_invoice_browser_verifies_page_scoped_payment_review_without_claiming_failed_charge():
    script = app().get("/admin/client.js").text
    assert "history.payment_review" in script
    assert 'review.scope !== "verified_first_page_only"' in script
    assert "review.open_with_balance_count !== open.length" in script
    assert "review.uncollectible_count !== uncollectible.length" in script
    assert "review.complete_history !== !history.has_more" in script
    assert "Invoice review summary mismatch" in script
    assert "Partial invoice history" in script
    assert "not revenue or overdue determination" in script
    assert "innerHTML" not in script
