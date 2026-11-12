"""No-network Stripe provider tests: authoritative fetch and fail-closed behavior."""
import json
import urllib.error

import pytest

from guardoath.stripe_provider import fetch_subscription
from guardoath.subscription_reconciliation import StripeBinding, SubscriptionReconciliationError

BINDING = StripeBinding("usr-acme", "cus_acme", "sub_acme")
KEY = "sk_test_" + "a" * 32


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


def fake(data, *, calls=None):
    def opener(request, timeout):
        if calls is not None:
            calls.append((request.full_url, request.get_header("Authorization"), timeout))
        return Response(data)
    return opener


def subscription(status="active", customer="cus_acme"):
    return json.dumps({"object": "subscription", "id": "sub_acme",
                       "customer": customer, "status": status}).encode()


def test_provider_lookup_uses_fixed_stripe_host_and_server_credential():
    calls = []
    result = fetch_subscription(BINDING, KEY, opener=fake(subscription(), calls=calls))
    assert result.active and result.tenant_id == "usr-acme"
    assert calls[0][0] == "https://api.stripe.com/v1/subscriptions/sub_acme"
    assert calls[0][1].startswith("Basic ")
    assert calls[0][2] == 5.0


@pytest.mark.parametrize("payload", [
    subscription(status="past_due"), subscription(customer="cus_other"),
    b"not-json", b"[]" , b"x" * 262145,
])
def test_provider_does_not_activate_on_bad_or_nonactive_state(payload):
    if payload == subscription(status="past_due"):
        assert not fetch_subscription(BINDING, KEY, opener=fake(payload)).active
    else:
        with pytest.raises(SubscriptionReconciliationError):
            fetch_subscription(BINDING, KEY, opener=fake(payload))


def test_provider_failure_is_not_treated_as_paid():
    def unavailable(request, timeout):
        raise urllib.error.URLError("unavailable")
    with pytest.raises(SubscriptionReconciliationError):
        fetch_subscription(BINDING, KEY, opener=unavailable)


def test_missing_secret_fails_before_network():
    calls = []
    with pytest.raises(SubscriptionReconciliationError):
        fetch_subscription(BINDING, "", opener=fake(subscription(), calls=calls))
    assert calls == []



@pytest.mark.parametrize("invalid_key", [
    "sk_test_" + "a" * 20 + "\u00e9",
    "sk_test_" + "a" * 20 + " ",
    "sk_test_" + "a" * 20 + "\\n",
    "pk_test_" + "a" * 32,
])
def test_malformed_stripe_subscription_secret_never_reaches_transport(invalid_key):
    calls = []
    with pytest.raises(SubscriptionReconciliationError, match="credential"):
        fetch_subscription(BINDING, invalid_key, opener=fake(subscription(), calls=calls))
    assert calls == []


@pytest.mark.parametrize("timeout", [
    True, False, float("nan"), float("inf"), -float("inf"),
    "5", None, 0, -1, 10.01,
])
def test_invalid_subscription_timeout_never_reaches_transport(timeout):
    calls = []
    with pytest.raises(SubscriptionReconciliationError, match="timeout"):
        fetch_subscription(BINDING, KEY, timeout=timeout,
                           opener=fake(subscription(), calls=calls))
    assert calls == []


def test_bounded_integer_timeout_is_valid():
    calls = []
    assert fetch_subscription(BINDING, KEY, timeout=1,
                              opener=fake(subscription(), calls=calls)).active
    assert calls[0][2] == 1


def test_default_http_client_disables_redirects(monkeypatch):
    """The authorization header must not travel to a redirect destination."""
    import urllib.request
    built = []

    class DummyOpener:
        def open(self, request, timeout):
            return Response(subscription())

    def build(*handlers):
        built.extend(handlers)
        return DummyOpener()

    monkeypatch.setattr(urllib.request, "build_opener", build)
    assert fetch_subscription(BINDING, KEY).active
    assert len(built) == 1
    assert built[0].redirect_request(None, None, 302, "Found",
                                     {"Location": "https://attacker.example"},
                                     "https://attacker.example") is None
