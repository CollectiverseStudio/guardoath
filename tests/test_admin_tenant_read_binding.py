"""Admin source-binding regression: an indexed tenant cannot resolve to another account.

All owner-sensitive reads audit first; mismatched saved identities fail closed
before usage projection or a Stripe subscription/invoice provider request.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guardoath.admin_console import VerifiedAdmin, create_admin_router

OWNER = "12345678-1234-1234-1234-123456789012"
REQUESTED = "usr-requested"
HEADERS = {"X-GuardOath-Admin-Session": "owner"}

PATHS = (
    (f"/v1/admin/tenants/{REQUESTED}", "tenant.detail"),
    (f"/v1/admin/tenants/{REQUESTED}/usage", "tenant.usage"),
    (f"/v1/admin/subscriptions/{REQUESTED}", "stripe.subscription"),
    (f"/v1/admin/subscriptions/{REQUESTED}/invoices", "stripe.invoices"),
)


def saved(tenant_id=REQUESTED):
    return SimpleNamespace(
        tenant_id=tenant_id, display_name="Saved account", demo=False,
        status="active", subscription_status="inactive",
        billing_mode="stripe", monthly_allowance=100, revision=2,
        usage={datetime.now(timezone.utc).strftime("%Y-%m"): 7},
        overage="block", updated_at="2026-11-22T09:00:00Z",
        key_hashes=(), payer_id="cus_private123",
        stripe_subscription_id="sub_private123",
    )


def client_for(record):
    operations = []

    class Store:
        def get(self, tenant_id):
            operations.append(("store", tenant_id))
            assert tenant_id == REQUESTED
            return record

    class Auditor:
        def log(self, **kw):
            operations.append(("audit", kw))
            return "a" * 64

    class Subscription:
        def read(self, tenant):
            operations.append(("subscription_provider", tenant.tenant_id))
            return {"verified": True, "status": "past_due",
                    "subscription_entitlement": False}

    class Invoices:
        def read(self, tenant):
            operations.append(("invoice_provider", tenant.tenant_id))
            return {"status": "verified", "verified": True, "invoices": [],
                    "count": 0, "has_more": False}

    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: (
            VerifiedAdmin(OWNER, frozenset({"owner"}), True)
            if token == "owner" else None),
        tenant_store=Store(), read_auditor=Auditor(),
        billing_reporter=Subscription(), billing_invoices=Invoices(),
        require_persistent_read_audit=True,
    ))
    return TestClient(app), operations


@pytest.mark.parametrize("path,resource", PATHS)
@pytest.mark.parametrize("stored_id", ("usr-other", None, False, "usr-requested "))
def test_wrong_saved_tenant_never_reaches_projections_or_stripe(path, resource, stored_id):
    client, operations = client_for(saved(stored_id))
    result = client.get(path, headers=HEADERS)
    assert result.status_code == 503
    assert "Saved account" not in result.text
    assert "usr-other" not in result.text
    assert "cus_private" not in result.text
    assert "sub_private" not in result.text
    assert [op[0] for op in operations] == ["audit", "store"]
    assert operations[0][1] == {
        "actor_sub": OWNER, "resource": resource, "tenant_id": REQUESTED}


@pytest.mark.parametrize("path,resource", PATHS)
def test_exact_saved_tenant_identity_preserves_authorized_read(path, resource):
    client, operations = client_for(saved())
    assert client.get(path).status_code == 401
    assert operations == []
    assert client.get(path, headers={"X-GuardOath-Key": "owner"}).status_code == 401
    assert operations == []
    result = client.get(path, headers=HEADERS)
    assert result.status_code == 200, result.text
    assert result.json()["read_audit_event_id"] == "a" * 64
    assert [op[0] for op in operations][:2] == ["audit", "store"]
    assert operations[0][1]["resource"] == resource
    if resource == "tenant.usage":
        assert result.json()["usage"]["tenant_id"] == REQUESTED
        assert result.json()["usage"]["requests_recorded"] == 7
    elif resource == "tenant.detail":
        assert result.json()["tenant"]["tenant_id"] == REQUESTED
    elif resource == "stripe.subscription":
        assert [op[0] for op in operations] == ["audit", "store", "subscription_provider"]
        assert result.json()["subscription"]["subscription_entitlement"] is False
    elif resource == "stripe.invoices":
        assert [op[0] for op in operations] == ["audit", "store", "invoice_provider"]
        assert result.json()["history"]["verified"] is True
