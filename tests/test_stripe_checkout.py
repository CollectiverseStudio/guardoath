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
    return json.dumps({"object": "checkout.session", "customer": "cus_test",
                       "mode": "subscription", "url": url, **changes}).encode()


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
    b"not json",
    b"x" * 65537,
])
def test_untrusted_provider_response_is_rejected(payload):
    with pytest.raises(StripeCheckoutError):
        create_checkout_session(**ARGS, opener=lambda request, timeout: Response(payload))
