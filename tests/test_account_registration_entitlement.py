"""Registration is identity-only; no payment entitlement or validation access is minted."""
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_store import MemoryTenantStore
from guardoath.tenant_sessions import SESSION_HEADER


class VerifiedSessionStub:
    """Isolate HTTP account routing; cryptographic Cognito checks have their own suite."""
    registration_enabled = True

    def tenant_for(self, token):
        return "usr-test-account" if token == "verified-test-token" else None

    def public_config(self):
        return {"registration_enabled": True, "google_provider": "Google"}


def make_client():
    store = MemoryTenantStore()
    app = create_app(engine=demo_engine(), credentials={},
                     tenants=TenantService(store, default_checkers(), "test-session-secret-with-minimum-32-bytes"),
                     sessions=VerifiedSessionStub())
    return TestClient(app), store


def test_newly_signed_in_user_registers_without_subscription_and_cannot_run_paid_checks():
    client, store = make_client()
    headers = {SESSION_HEADER: "verified-test-token"}
    initial = client.get("/v1/account/status", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["registered"] is False
    assert initial.json()["can_validate"] is False
    assert initial.json()["subscription_status"] == "not_registered"
    assert initial.json()["tenant_id"] is None
    assert client.post("/v1/register").status_code == 401
    assert client.post("/v1/register", headers={SESSION_HEADER: "invalid"}).status_code == 401

    created = client.post("/v1/register", headers=headers, json={
        "subscription_status": "active", "monthly_allowance": 1000000
    })
    assert created.status_code == 201, created.text
    assert created.json()["subscription_status"] == "inactive"
    assert created.json()["can_validate"] is False
    tenant = store.get("usr-test-account")
    assert tenant is not None and not tenant.demo
    assert tenant.subscription_status == "inactive"
    assert tenant.monthly_allowance == 0
    assert tenant.key_hashes == ()

    status = client.get("/v1/account/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["registered"] is True
    assert status.json()["subscription_status"] == "inactive"
    assert status.json()["can_validate"] is False
    assert client.post("/v1/register", headers=headers).status_code == 409

    response = client.post("/v1/checks/input", headers=headers, json={
        "application_id": "usr-test-account",
        "content": "Hi", "content_format": "text"
    })
    assert response.status_code == 402
    assert store.get("usr-test-account").usage == {}
    assert client.get("/v1/account/status", headers={SESSION_HEADER: "invalid"}).status_code == 401


def test_account_status_is_never_derived_from_unverified_session_or_shared_api_key():
    client, _store = make_client()
    assert client.get("/v1/account/status").status_code == 401
    assert client.get("/v1/account/status", headers={"X-GuardOath-Key": "gok_fake"}).status_code == 401
