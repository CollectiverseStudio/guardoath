"""Existing authenticated owner UI renders signed read receipts without exposing account email."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from guardoath.admin_browser import BrowserAuthConfig, BoundExchange, create_browser_router
from guardoath.admin_console import VerifiedAdmin


def test_owner_browser_read_receipt_and_mutation_receipt_are_separately_validated():
    app = FastAPI()
    cfg = BrowserAuthConfig(
        "acme.auth.us-east-1.amazoncognito.com", "testclient123",
        "https://guardoath.com/admin/login")
    app.include_router(create_browser_router(
        cfg, BoundExchange(cfg, sender=lambda url, body: {"access_token": "a" * 40}),
        lambda _: VerifiedAdmin("subject", frozenset({"owner"}), True)))
    client = TestClient(app)
    page = client.get("/admin/login")
    script = client.get("/admin/client.js")
    assert page.status_code == script.status_code == 200
    assert 'id="audit-go"' in page.text and 'id="user-inspect"' in page.text
    for fragment in ("function readAuditLabel(result)", "readAuditLabel(result)",
                     "readAuditLabel(response)", 'receipt.operation === "admin.read"',
                     'receipt.integrity_status !== "signed_verified"',
                     'change.field !== "protected_read"',
                     '"cognito.exact_user","stripe.subscription","audit.receipt"',
                     '"tenant.directory","tenant.detail","tenant.usage","catalog.registry","catalog.preview"',
                     'readAuditLabel(overview)', 'readAuditLabel(available)',
                     "Persistent read auditing not configured",
                     'readAuditLabel(catalog)', 'readAuditLabel(data)',
                     "receipt.new_revision !== receipt.prior_revision + 1"):
        assert fragment in script.text
    assert "innerHTML" not in script.text


def test_audit_viewer_accepts_exactly_the_server_supported_sensitive_read_resources():
    """Keep the authenticated browser receipt verifier in parity with signed API types.

    No independently introduced client-side resource or omitted server-side
    receipt can silently appear approved as new protected reads are added.
    """
    import json
    import re
    from guardoath.admin_audit import SENSITIVE_READS
    from guardoath.admin_browser import _JS
    # Match only the literal receipt-resource array. Other interactive controls
    # legitimately use unrelated ![...].includes(...) guards earlier in the script.
    match = re.search(r'!\[([^\]]*?)\]\.includes\(change\.resource\)', _JS, re.S)
    assert match is not None
    options = json.loads("[" + match.group(1) + "]")
    assert len(options) == len(set(options))
    assert set(options) == SENSITIVE_READS
    assert "admin.overview" in options and "admin.features" in options
