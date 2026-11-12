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



def test_owner_billing_portal_refuses_cross_customer_provider_response_without_entitlement(monkeypatch):
    """The real owner HTTP route must never relay another tenant's portal."""
    import json
    from guardoath import stripe_portal
    from guardoath.billing import BillingMode
    from guardoath.tenant_record import TenantRecord
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", "sk_test_" + "a" * 32)

    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self, size):
            return json.dumps({
                "id": "bps_test_wrong_customer",
                "object": "billing_portal.session",
                "customer": "cus_other", "livemode": False,
                "url": "https://billing.stripe.com/p/session/other",
            }).encode()[:size]

    class FakeOpener:
        def open(self, request, timeout):
            assert timeout == 5
            assert request.full_url == "https://api.stripe.com/v1/billing_portal/sessions"
            return FakeResponse()

    monkeypatch.setattr(stripe_portal.urllib.request, "build_opener",
                        lambda *_handlers: FakeOpener())
    client, store = make_client()
    store.create(TenantRecord(
        tenant_id="usr-test-account", display_name="Unpaid Stripe account",
        demo=False, status="active", monthly_allowance=0, key_hashes=(),
        input_check_text_security="block", output_check_text_security="block",
        subscription_status="inactive", billing_mode=BillingMode.STRIPE,
        payer_id="cus_owned",
    ))
    headers = {SESSION_HEADER: "verified-test-token"}
    response = client.post("/v1/billing/portal", headers=headers, json={})
    assert response.status_code == 503, response.text
    assert "portal_url" not in response.text
    assert store.get("usr-test-account").payer_id == "cus_owned"
    assert store.get("usr-test-account").subscription_status == "inactive"
    assert store.get("usr-test-account").key_hashes == ()
    assert client.post("/v1/tenants/current/keys", headers=headers, json={}).status_code == 402


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



def test_real_checkout_adapter_rejects_cross_tenant_provider_session_without_entitlement(monkeypatch):
    """Exercise the real Checkout adapter through the signed-in HTTP route.

    The returned hosted URL cannot authorize a different tenant, even when
    the provider customer ID matches. No outside Stripe calls or payments.
    """
    import json
    from guardoath import stripe_customer, stripe_checkout
    monkeypatch.setattr(stripe_customer, "create_customer", lambda **_kw: "cus_verified")
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", "sk_test_" + "a" * 32)
    monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID", "price_verified")

    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self, size):
            return json.dumps({
                "id": "cs_test_other_tenant", "object": "checkout.session",
                "customer": "cus_verified",
                "client_reference_id": "usr-other-tenant",
                "mode": "subscription", "status": "open", "livemode": False,
                "url": "https://checkout.stripe.com/c/pay/cs_test_other_tenant",
            }).encode()[:size]

    class FakeOpener:
        def open(self, request, timeout):
            assert request.full_url == "https://api.stripe.com/v1/checkout/sessions"
            assert timeout == 5
            return FakeResponse()

    monkeypatch.setattr(stripe_checkout.urllib.request, "build_opener",
                        lambda *_handlers: FakeOpener())
    client, store = make_client()
    owner = {SESSION_HEADER: "verified-test-token"}
    assert client.post("/v1/register", headers=owner).status_code == 201
    failed = client.post("/v1/billing/checkout", headers=owner, json={})
    assert failed.status_code == 503, failed.text
    assert "checkout_url" not in failed.text
    tenant = store.get("usr-test-account")
    assert tenant.payer_id == "cus_verified"
    assert tenant.subscription_status == "inactive"
    assert tenant.key_hashes == () and tenant.usage == {}
    assert client.get("/v1/account/status", headers=owner).json()["can_validate"] is False
    assert client.post("/v1/tenants/current/keys", headers=owner, json={}).status_code == 402

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
    from guardoath.tenant_record import new_key
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


def test_signed_in_customer_can_list_create_and_revoke_keys_only_with_active_plan():
    """Owner sessions manage keys; API keys remain separate from account authority."""
    from guardoath.tenant_record import TenantRecord, new_key
    client, store = make_client()
    existing_token, existing_digest = new_key("usr-test-account")
    store.create(TenantRecord(
        tenant_id="usr-test-account", display_name="Subscribed account",
        status="active", demo=False, monthly_allowance=100,
        key_hashes=(existing_digest,), input_check_text_security="block",
        output_check_text_security="block", subscription_status="active",
    ))
    headers = {SESSION_HEADER: "verified-test-token"}
    listed = client.get("/v1/tenants/current/keys", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["keys"] == [{"key_id": existing_digest[:12]}]
    assert "api_key" not in listed.text
    created = client.post("/v1/tenants/current/keys", headers=headers, json={})
    assert created.status_code == 200, created.text
    new_token = created.json()["api_key"]
    assert new_token != existing_token
    assert len(store.get("usr-test-account").key_hashes) == 2
    assert client.post("/v1/tenants/current/keys", headers=headers, json={}).status_code == 409
    assert client.post("/v1/tenants/current/keys/revoke", headers=headers,
                       json={"key_id": existing_digest[:12]}).status_code == 200
    assert len(store.get("usr-test-account").key_hashes) == 1
    assert existing_digest not in store.get("usr-test-account").key_hashes
    assert client.get("/v1/tenants/current/keys", headers={
        "X-GuardOath-Key": new_token
    }).status_code == 403


def test_public_subscription_price_is_provider_verified_and_read_only(monkeypatch):
    """Disclose the real recurring amount, without creating a payer or paid access."""
    import json
    import urllib.request
    from guardoath import stripe_customer

    price = {
        "id": "price_verified", "object": "price", "active": True,
        "currency": "usd", "unit_amount": 1299,
        "recurring": {"interval": "month", "interval_count": 1},
    }
    calls = []
    class ProviderResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self, max_bytes):
            assert max_bytes == 16385
            return json.dumps(price).encode("utf-8")
    class ProviderOpener:
        def open(self, request, timeout):
            assert timeout == 5
            assert request.full_url == "https://api.stripe.com/v1/prices/price_verified"
            assert request.get_header("Authorization").startswith("Basic ")
            calls.append(request.full_url)
            return ProviderResponse()
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: ProviderOpener())
    monkeypatch.setattr(stripe_customer, "create_customer",
                        lambda **kwargs: (_ for _ in ()).throw(
                            AssertionError("Price lookup must never create Stripe customers")))
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", "sk_test_" + "a" * 32)
    monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID", "price_verified")

    client, store = make_client()
    headers = {SESSION_HEADER: "verified-test-token"}
    assert client.post("/v1/register", headers=headers).status_code == 201
    before = store.get("usr-test-account")
    disclosed = client.get("/v1/billing/price")
    assert disclosed.status_code == 200, disclosed.text
    assert disclosed.json() == {
        "unit_amount": 1299, "currency": "USD",
        "currency_exponent": 2, "interval": "month",
    }
    assert disclosed.headers["cache-control"] == "no-store"
    assert calls == ["https://api.stripe.com/v1/prices/price_verified"]
    assert store.get("usr-test-account") == before
    assert store.get("usr-test-account").payer_id is None
    assert store.get("usr-test-account").subscription_status == "inactive"
    assert client.post("/v1/tenants/current/keys", headers=headers, json={}).status_code == 402

    # Accept a real Stripe zero-decimal currency without inventing cents.
    price.update(currency="jpy", unit_amount=1500)
    yen = client.get("/v1/billing/price")
    assert yen.status_code == 200
    assert yen.json()["currency_exponent"] == 0

    # A stale/disabled/mismatched or unsupported provider response must NOT
    # publish a price that could trigger customer checkout in the dashboard.
    for mutation in (
        {"active": False},
        {"id": "price_other"},
        {"unit_amount": -1},
        {"unit_amount": True},
        {"recurring": {"interval": "week", "interval_count": 1}},
        {"recurring": {"interval": "month", "interval_count": 2}},
    ):
        price.update({
            "id": "price_verified", "active": True,
            "unit_amount": 1299, "currency": "usd",
            "recurring": {"interval": "month", "interval_count": 1},
        })
        price.update(mutation)
        unavailable = client.get("/v1/billing/price")
        assert unavailable.status_code == 503, (mutation, unavailable.text)
    assert store.get("usr-test-account") == before


def test_public_subscription_price_never_calls_provider_without_server_configuration(monkeypatch):
    import urllib.request
    monkeypatch.delenv("GUARDOATH_STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("GUARDOATH_STRIPE_PRICE_ID", raising=False)
    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *args: (_ for _ in ()).throw(
                            AssertionError("No provider request without secret and price")))
    client, store = make_client()
    reply = client.get("/v1/billing/price")
    assert reply.status_code == 503
    assert store.tenant_ids() == ()


def test_owner_reconciles_verified_subscription_and_paid_quota_without_browser_claims(monkeypatch):
    """End-to-end signed-in API + authoritative memory tenant write, offline Stripe."""
    from guardoath import stripe_provider
    from guardoath.subscription_reconciliation import StripeBinding
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", "sk_test_" + "a" * 32)
    monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID", "price_starter")
    monkeypatch.setenv("GUARDOATH_PAID_MONTHLY_ALLOWANCE", "125")
    calls = []
    def verified(**kwargs):
        calls.append(kwargs)
        return StripeBinding(kwargs["tenant_id"], kwargs["customer_id"], "sub_verified"), {
            "object": "subscription", "id": "sub_verified",
            "customer": kwargs["customer_id"], "status": "active",
        }
    monkeypatch.setattr(stripe_provider, "verified_subscription_snapshot", verified)
    client, store = make_client()
    owner = {SESSION_HEADER: "verified-test-token"}
    assert client.post("/v1/register", headers=owner).status_code == 201
    current = store.get("usr-test-account")
    assert current.monthly_allowance == 0
    assert client.post("/v1/billing/reconcile", headers=owner, json={}).status_code == 409
    store.bind_stripe_customer(current.tenant_id, "cus_owned", current.revision)
    assert client.post("/v1/billing/reconcile", json={}).status_code == 401
    assert client.post("/v1/billing/reconcile",
                       headers={SESSION_HEADER: "invalid"}, json={}).status_code == 401
    response = client.post("/v1/billing/reconcile", headers=owner, json={
        "tenant_id": "usr-other", "subscription_status": "active",
        "monthly_allowance": 1000000,
    })
    assert response.status_code == 200, response.text
    assert response.json()["subscription_status"] == "active"
    assert response.json()["monthly_allowance"] == 125
    assert response.json()["can_validate"] is True
    assert calls and calls[0]["tenant_id"] == "usr-test-account"
    assert calls[0]["customer_id"] == "cus_owned"
    assert calls[0]["price_id"] == "price_starter"
    tenant = store.get("usr-test-account")
    assert tenant.stripe_subscription_id == "sub_verified"
    assert tenant.monthly_allowance == 125
    assert tenant.overage == "block"
    assert tenant.key_hashes == ()
    assert client.get("/v1/account/status", headers=owner).json()["can_validate"] is True
    created_key = client.post("/v1/tenants/current/keys", headers=owner, json={})
    assert created_key.status_code == 200, created_key.text


def test_failed_subscription_reconciliation_cannot_mint_entitlement(monkeypatch):
    from guardoath import stripe_provider
    from guardoath.subscription_reconciliation import SubscriptionReconciliationError
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", "sk_test_" + "a" * 32)
    monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID", "price_starter")
    def unavailable(**kwargs):
        raise SubscriptionReconciliationError("Untrusted provider state")
    monkeypatch.setattr(stripe_provider, "verified_subscription_snapshot", unavailable)
    client, store = make_client()
    owner = {SESSION_HEADER: "verified-test-token"}
    assert client.post("/v1/register", headers=owner).status_code == 201
    initial = store.get("usr-test-account")
    bound = store.bind_stripe_customer(initial.tenant_id, "cus_owned", initial.revision)
    for allowance in ("", "0", "-1", "true", "1000000001"):
        monkeypatch.setenv("GUARDOATH_PAID_MONTHLY_ALLOWANCE", allowance)
        refused = client.post("/v1/billing/reconcile", headers=owner, json={})
        assert refused.status_code == 503
        assert store.get(bound.tenant_id) == bound
    monkeypatch.setenv("GUARDOATH_PAID_MONTHLY_ALLOWANCE", "125")
    response = client.post("/v1/billing/reconcile", headers=owner, json={})
    assert response.status_code == 503
    assert "subscription_status" not in response.text
    assert store.get(bound.tenant_id) == bound
    assert client.post("/v1/tenants/current/keys", headers=owner, json={}).status_code == 402


def test_reconciled_past_due_revokes_paid_quota_and_validation(monkeypatch):
    from guardoath import stripe_provider
    from guardoath.subscription_reconciliation import StripeBinding
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", "sk_test_" + "a" * 32)
    monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID", "price_starter")
    monkeypatch.setenv("GUARDOATH_PAID_MONTHLY_ALLOWANCE", "125")
    state = ["active"]
    def verified(**kwargs):
        return StripeBinding(kwargs["tenant_id"], kwargs["customer_id"], "sub_verified"), {
            "object": "subscription", "id": "sub_verified",
            "customer": kwargs["customer_id"], "status": state[0],
        }
    monkeypatch.setattr(stripe_provider, "verified_subscription_snapshot", verified)
    client, store = make_client()
    owner = {SESSION_HEADER: "verified-test-token"}
    client.post("/v1/register", headers=owner)
    initial = store.get("usr-test-account")
    store.bind_stripe_customer(initial.tenant_id, "cus_owned", initial.revision)
    assert client.post("/v1/billing/reconcile", headers=owner, json={}).status_code == 200
    state[0] = "past_due"
    revoked = client.post("/v1/billing/reconcile", headers=owner, json={})
    assert revoked.status_code == 200
    assert revoked.json()["subscription_status"] == "past_due"
    assert revoked.json()["can_validate"] is False
    assert revoked.json()["monthly_allowance"] == 0
    assert store.get("usr-test-account").stripe_subscription_id == "sub_verified"
    assert client.post("/v1/tenants/current/keys", headers=owner, json={}).status_code == 402
