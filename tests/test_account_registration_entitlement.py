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


def test_checkout_route_requires_verified_session_and_server_provisioning():
    client, store = make_client()
    headers = {SESSION_HEADER: "verified-test-token"}
    # Billing has no customer-supplied price/status body, but global JSON
    # admission still applies to POST. Authenticate a correctly typed empty
    # request; an untyped body is independently rejected without a provider call.
    assert client.post("/v1/billing/checkout", content=b"{}").status_code == 415
    assert client.post("/v1/billing/checkout", json={}).status_code == 401
    assert client.post("/v1/billing/checkout", headers={SESSION_HEADER: "invalid"},
                       json={}).status_code == 401
    assert client.post("/v1/billing/checkout", headers=headers, json={}).status_code == 403
    assert client.post("/v1/register", headers=headers).status_code == 201
    response = client.post("/v1/billing/checkout", headers=headers, json={})
    assert response.status_code == 503, response.text
    assert store.get("usr-test-account").subscription_status == "inactive"
    assert store.get("usr-test-account").usage == {}


def test_billing_portal_requires_owner_and_existing_stripe_customer():
    client, store = make_client()
    headers = {SESSION_HEADER: "verified-test-token"}
    assert client.post("/v1/billing/portal", content=b"{}").status_code == 415
    assert client.post("/v1/billing/portal", json={}).status_code == 401
    assert client.post("/v1/billing/portal", headers={SESSION_HEADER: "invalid"},
                       json={}).status_code == 401
    assert client.post("/v1/billing/portal", headers=headers, json={}).status_code == 403
    assert client.post("/v1/register", headers=headers).status_code == 201
    response = client.post("/v1/billing/portal", headers=headers, json={})
    assert response.status_code == 409, response.text
    assert store.get("usr-test-account").subscription_status == "inactive"


def test_disabled_registered_identity_cannot_register_again_or_appear_unregistered():
    # Existing disabled tenants are not new customers; a misleading
    # 'not registered' status would otherwise cause an impossible retry loop.
    from guardoath.tenant_record import TenantRecord
    client, store = make_client()
    store.create(TenantRecord(
        tenant_id="usr-test-account", display_name="Disabled account",
        status="disabled", demo=False, monthly_allowance=0,
        key_hashes=(), input_check_text_security="block",
        output_check_text_security="block", subscription_status="inactive",
    ))
    headers = {SESSION_HEADER: "verified-test-token"}
    status = client.get("/v1/account/status", headers=headers)
    assert status.status_code == 403
    assert status.json() == {"detail": "Account is unavailable; contact support"}
    assert client.post("/v1/register", headers=headers, json={}).status_code == 409
    assert client.get("/v1/applications/current", headers=headers).status_code == 401
    assert store.get("usr-test-account").status == "disabled"
    assert store.get("usr-test-account").usage == {}


def test_stripe_checkout_provisions_customer_without_granting_access(monkeypatch):
    """A valid Stripe checkout URL is not payment confirmation."""
    from guardoath import stripe_checkout, stripe_customer
    calls = []
    def customer(**kwargs):
        calls.append(("customer", kwargs["tenant_id"]))
        return "cus_verified"
    def checkout(**kwargs):
        calls.append(("checkout", kwargs["customer_id"], kwargs["tenant_id"]))
        return "https://checkout.stripe.com/c/pay/cs_test_safe"
    monkeypatch.setattr(stripe_customer, "create_customer", customer)
    monkeypatch.setattr(stripe_checkout, "create_checkout_session", checkout)
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", "sk_test_" + "a" * 32)
    monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID", "price_verified")
    client, store = make_client()
    headers = {SESSION_HEADER: "verified-test-token"}
    assert client.post("/v1/register", headers=headers).status_code == 201
    response = client.post("/v1/billing/checkout", headers=headers, json={})
    assert response.status_code == 200, response.text
    assert response.json()["checkout_url"].startswith("https://checkout.stripe.com/")
    assert calls == [("customer", "usr-test-account"),
                     ("checkout", "cus_verified", "usr-test-account")]
    tenant = store.get("usr-test-account")
    assert tenant.payer_id == "cus_verified"
    assert tenant.subscription_status == "inactive"
    assert tenant.key_hashes == () and tenant.usage == {}
    assert client.get("/v1/account/status", headers=headers).json()["can_validate"] is False
    assert client.post("/v1/tenants/current/keys", headers=headers, json={}).status_code == 402
    # A second checkout reuses the authoritative Stripe customer binding.
    assert client.post("/v1/billing/checkout", headers=headers, json={}).status_code == 200
    assert [call[0] for call in calls].count("customer") == 1


def test_checkout_provider_failure_never_activates_subscription(monkeypatch):
    from guardoath import stripe_checkout, stripe_customer
    monkeypatch.setattr(stripe_customer, "create_customer", lambda **kwargs: "cus_verified")
    def fail(**kwargs):
        raise stripe_checkout.StripeCheckoutError("Provider unavailable")
    monkeypatch.setattr(stripe_checkout, "create_checkout_session", fail)
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", "sk_test_" + "a" * 32)
    monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID", "price_verified")
    client, store = make_client()
    headers = {SESSION_HEADER: "verified-test-token"}
    assert client.post("/v1/register", headers=headers).status_code == 201
    response = client.post("/v1/billing/checkout", headers=headers, json={})
    assert response.status_code == 503
    tenant = store.get("usr-test-account")
    assert tenant.subscription_status == "inactive"
    assert tenant.key_hashes == () and tenant.usage == {}
    assert client.post("/v1/tenants/current/keys", headers=headers, json={}).status_code == 402


def test_missing_stripe_price_does_not_create_or_bind_customer(monkeypatch):
    """Incomplete billing configuration must fail before any Stripe write."""
    from guardoath import stripe_customer
    calls = []
    def unexpected_customer(**kwargs):
        calls.append(kwargs)
        raise AssertionError("Customer must not be provisioned")
    monkeypatch.setattr(stripe_customer, "create_customer", unexpected_customer)
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", "sk_test_" + "a" * 32)
    monkeypatch.delenv("GUARDOATH_STRIPE_PRICE_ID", raising=False)
    client, store = make_client()
    headers = {SESSION_HEADER: "verified-test-token"}
    assert client.post("/v1/register", headers=headers).status_code == 201
    response = client.post("/v1/billing/checkout", headers=headers, json={})
    assert response.status_code == 503
    assert calls == []
    tenant = store.get("usr-test-account")
    assert tenant.payer_id is None
    assert tenant.subscription_status == "inactive"
    assert tenant.key_hashes == ()


def test_malformed_stripe_configuration_never_creates_customer(monkeypatch):
    from guardoath import stripe_customer
    calls = []
    monkeypatch.setattr(stripe_customer, "create_customer",
                        lambda **kwargs: calls.append(kwargs))
    for secret, price in (
        ("pk_test_public", "price_valid"),
        ("sk_test_" + "a" * 32, "not-a-price"),
        ("sk_test_" + "a" * 32, "price_"),
        ("sk_test_" + "a" * 32, "price_invalid value"),
    ):
        monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", secret)
        monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID", price)
        client, store = make_client()
        headers = {SESSION_HEADER: "verified-test-token"}
        assert client.post("/v1/register", headers=headers).status_code == 201
        assert client.post("/v1/billing/checkout", headers=headers, json={}).status_code == 503
        assert store.get("usr-test-account").payer_id is None
        assert store.get("usr-test-account").subscription_status == "inactive"
    assert calls == []


def test_application_api_key_cannot_manage_customer_keys():
    """Programmatic API credentials are never account-management sessions."""
    from guardoath.tenant_record import TenantRecord
    from guardoath.tenant_store import new_key
    client, store = make_client()
    token, digest = new_key("usr-test-account")
    store.create(TenantRecord(
        tenant_id="usr-test-account", display_name="Paid customer",
        status="active", demo=False, monthly_allowance=100,
        key_hashes=(digest,), input_check_text_security="block",
        output_check_text_security="block", subscription_status="active",
    ))
    api_headers = {"X-GuardOath-Key": token}
    assert client.get("/v1/tenants/current/keys", headers=api_headers).status_code == 403
    assert client.post("/v1/tenants/current/keys", headers=api_headers,
                       json={}).status_code == 403
    assert client.post("/v1/tenants/current/keys/revoke", headers=api_headers,
                       json={"key_id": digest[:12]}).status_code == 403
    assert store.get("usr-test-account").key_hashes == (digest,)
