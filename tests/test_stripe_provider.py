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
