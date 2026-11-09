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
    # Preserve the tenant schema's at-least-one-check invariant without
    # conferring any paid use: the baseline is local hidden-text protection.
    assert tenant.input_check_text_security == "block"
    assert tenant.output_check_text_security == "block"

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


def test_registration_allows_only_empty_untyped_post_and_retains_global_ingress_controls():
    client, store = make_client()
    headers = {SESSION_HEADER: "verified-test-token"}
    # No special allowance for untyped data: registration is identity-only.
    assert client.post("/v1/register", headers=headers,
                       content=b'{"subscription_status":"active"}').status_code == 415
    assert client.post("/v1/register", headers={**headers, "Content-Type": "text/plain"},
                       content=b"{}").status_code == 415
    # Neither a missing media type nor an empty body bypasses validation on other endpoints.
    assert client.post("/v1/checks/input", headers=headers).status_code == 415
    assert store.get("usr-test-account") is None
    # A genuine empty registration request uses the verified session to bind the account.
    result = client.post("/v1/register", headers=headers)
    assert result.status_code == 201, result.text
    record = store.get("usr-test-account")
    assert record is not None and record.subscription_status == "inactive"
    assert record.key_hashes == () and record.monthly_allowance == 0


def test_registered_without_payment_cannot_issue_customer_api_key():
    client, store = make_client()
    headers = {SESSION_HEADER: "verified-test-token"}
    created = client.post("/v1/register", headers=headers)
    assert created.status_code == 201
    # No bearer key is minted by account creation or subsequent owner requests
    # while the authoritative subscription is inactive.
    denied = client.post("/v1/tenants/current/keys", headers=headers, json={})
    assert denied.status_code == 402, denied.text
    assert "subscription" in denied.json()["detail"]
    record = store.get("usr-test-account")
    assert record.subscription_status == "inactive"
    assert record.key_hashes == () and record.usage == {}


def test_unpaid_registration_cannot_run_output_checks_or_accrue_usage():
    """Input and output must enforce the same subscription boundary."""
    client, store = make_client()
    headers = {SESSION_HEADER: "verified-test-token"}
    assert client.post("/v1/register", headers=headers).status_code == 201
    for stage in ("input", "output"):
        response = client.post(f"/v1/checks/{stage}", headers=headers, json={
            "application_id": "usr-test-account",
            "content": "Harmless test content", "content_format": "text",
        })
        assert response.status_code == 402, (stage, response.text)
        assert store.get("usr-test-account").usage == {}
    assert store.get("usr-test-account").key_hashes == ()
