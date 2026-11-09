"""Stripe portal client rejects untrusted customer IDs and provider redirects."""
import json
import urllib.parse
import pytest

from guardoath.stripe_portal import StripePortalError, create_portal_session

KEY = "sk_test_" + "a" * 32


class Response:
    status = 200
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, size):
        return self.data[:size]


def payload(url="https://billing.stripe.com/p/session/test"):
    return json.dumps({"object": "billing_portal.session", "url": url}).encode()


def test_portal_uses_server_customer_and_fixed_return():
    requests = []
    def send(request, timeout):
        requests.append(request)
        assert timeout == 5
        return Response(payload())
    result = create_portal_session(customer_id="cus_owned", api_key=KEY, opener=send)
    assert result.startswith("https://billing.stripe.com/")
    assert requests[0].full_url == "https://api.stripe.com/v1/billing_portal/sessions"
    params = urllib.parse.parse_qs(requests[0].data.decode())
    assert params == {"customer": ["cus_owned"], "return_url": ["https://guardoath.com/dashboard"]}


@pytest.mark.parametrize("changes", [
    {"customer_id": "cus_"}, {"customer_id": "cus_invalid/path"},
    {"api_key": "pk_test_fake"}, {"return_url": "https://evil.example"},
])
def test_invalid_inputs_never_call_provider(changes):
    calls = []
    def send(request, timeout):
        calls.append(request)
        return Response(payload())
    with pytest.raises(StripePortalError):
        create_portal_session(**{**{"customer_id": "cus_owned", "api_key": KEY}, **changes}, opener=send)
    assert calls == []


@pytest.mark.parametrize("url", [
    "https://evil.example", "http://billing.stripe.com/",
    "https://billing.stripe.com.evil.example/", "https://billing.stripe.com:444/",
    "https://user@billing.stripe.com/", "https://billing.stripe.com:invalid/",
])
def test_provider_portal_url_must_be_trusted(url):
    with pytest.raises(StripePortalError):
        create_portal_session(customer_id="cus_owned", api_key=KEY,
                              opener=lambda request, timeout: Response(payload(url)))
