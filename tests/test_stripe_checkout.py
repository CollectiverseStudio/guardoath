"""Stripe checkout requests never grant access or accept customer-controlled prices."""
import json
import urllib.parse

import pytest

from guardoath.stripe_checkout import StripeCheckoutError, create_checkout_session

KEY = "sk_test_" + "a" * 32
ARGS = dict(customer_id="cus_test", price_id="price_basic", tenant_id="usr-test",
            api_key=KEY, success_url="https://guardoath.com/dashboard?checkout=success",
            cancel_url="https://guardoath.com/dashboard?checkout=cancel",
            idempotency_key="checkout-usr-test-123")


class Response:
    status = 200
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, limit):
        return self.data[:limit]


def checkout(url="https://checkout.stripe.com/c/pay/cs_test_123", **changes):
    return json.dumps({
        "object": "checkout.session", "id": "cs_test_123",
        "customer": "cus_test", "client_reference_id": "usr-test",
        "mode": "subscription", "status": "open", "livemode": False,
        "url": url, **changes,
    }).encode()


def test_checkout_uses_server_owned_customer_and_price():
    seen = []
    def opener(request, timeout):
        seen.append(request)
        assert timeout == 5
        return Response(checkout())
    url = create_checkout_session(**ARGS, opener=opener)
    assert url.startswith("https://checkout.stripe.com/")
    assert seen[0].full_url == "https://api.stripe.com/v1/checkout/sessions"
    form = urllib.parse.parse_qs(seen[0].data.decode())
    assert form["customer"] == ["cus_test"]
    assert form["line_items[0][price]"] == ["price_basic"]
    assert form["client_reference_id"] == ["usr-test"]
    assert form["mode"] == ["subscription"]


@pytest.mark.parametrize("change", [
    {"success_url": "https://evil.example/"},
    {"cancel_url": "https://guardoath.com.evil.example/"},
    {"price_id": "arbitrary"},
    {"customer_id": "other"},
    {"api_key": "pk_test_not_secret"},
    {"idempotency_key": "short"},
])
def test_invalid_checkout_configuration_is_rejected_before_network(change):
    calls = []
    def opener(request, timeout):
        calls.append(request)
        return Response(checkout())
    with pytest.raises(StripeCheckoutError):
        create_checkout_session(**{**ARGS, **change}, opener=opener)
    assert calls == []


@pytest.mark.parametrize("payload", [
    checkout(url="https://evil.example/"),
    checkout(url="http://checkout.stripe.com/session"),
    checkout(url="https://checkout.stripe.com.evil.example/session"),
    checkout(customer="cus_other"),
    checkout(client_reference_id="usr-other"),
    checkout(client_reference_id=None),
    checkout(id="not_a_checkout_session"),
    checkout(status="complete"),
    checkout(status=None),
    checkout(livemode=True),
    checkout(livemode=None),
    checkout(url="https://checkout.stripe.com/c/pay/cs_test_123#fragment"),
    checkout(url="https://checkout.stripe.com:444/c/pay/cs_test_123"),
    checkout(url="https://checkout.stripe.com:invalid/c/pay/cs_test_123"),
    b"not json",
    b"x" * 65537,
])
def test_untrusted_provider_response_is_rejected(payload):
    with pytest.raises(StripeCheckoutError):
        create_checkout_session(**ARGS, opener=lambda request, timeout: Response(payload))


def test_live_secret_requires_live_checkout_session_mode():
    live_args = {**ARGS, "api_key": "sk_live_" + "a" * 32}
    with pytest.raises(StripeCheckoutError, match="Invalid checkout provider response"):
        create_checkout_session(**live_args,
            opener=lambda request, timeout: Response(checkout(livemode=False)))

    url = create_checkout_session(**live_args,
        opener=lambda request, timeout: Response(checkout(
            id="cs_live_verified", livemode=True)))
    assert url == "https://checkout.stripe.com/c/pay/cs_test_123"


def test_customer_specific_checkout_session_requires_exact_reference_even_on_same_provider():
    # A provider response for a different tenant/customer must never be
    # presented as an eligible session, and cannot grant an entitlement.
    for response in (
        checkout(client_reference_id="usr-other"),
        checkout(customer="cus_other"),
        checkout(mode="payment"),
    ):
        with pytest.raises(StripeCheckoutError):
            create_checkout_session(**ARGS,
                opener=lambda request, timeout: Response(response))
