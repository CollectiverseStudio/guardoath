"""Offline Stripe subscription discovery: customer, price and environment verified."""
import json

import pytest

from guardoath.stripe_provider import discover_subscription
from guardoath.subscription_reconciliation import SubscriptionReconciliationError

KEY = "sk_test_" + "a" * 32
KW = dict(tenant_id="usr-acme", customer_id="cus_acme",
          price_id="price_starter", api_key=KEY)

class Response:
    status = 200
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, limit):
        return self.payload[:limit]

def subscription(**changes):
    return {
        "object": "subscription", "id": "sub_valid",
        "customer": "cus_acme", "livemode": False, "status": "active",
        "items": {"object": "list", "has_more": False,
                  "data": [{"price": {"id": "price_starter"}}]},
        **changes,
    }

def listing(*subscriptions, more=False):
    return json.dumps({"object": "list", "data": list(subscriptions),
                       "has_more": more}).encode()

def transport(payload, calls):
    def open(request, timeout):
        calls.append((request.full_url, request.get_header("Authorization"), timeout))
        return Response(payload)
    return open

def test_server_owned_customer_and_price_discover_one_subscription():
    calls = []
    binding, snapshot = discover_subscription(
        **KW, opener=transport(listing(subscription()), calls))
    assert (binding.tenant_id, binding.customer_id, binding.subscription_id) == (
        "usr-acme", "cus_acme", "sub_valid")
    assert snapshot["status"] == "active"
    assert len(calls) == 1
    assert calls[0][0].startswith("https://api.stripe.com/v1/subscriptions?")
    assert "customer=cus_acme" in calls[0][0]
    assert "status=all" in calls[0][0] and "limit=100" in calls[0][0]
    assert calls[0][1].startswith("Basic ")
    assert calls[0][2] == 5.0

@pytest.mark.parametrize("payload", [
    listing(), listing(subscription(), more=True),
    listing(subscription(), subscription(id="sub_second")),
    listing(subscription(customer="cus_other")),
    listing(subscription(livemode=True)),
    listing(subscription(items={"data": [], "has_more": False})),
    listing(subscription(items={"data": [], "has_more": True})),
    listing(subscription(items={"data": [{"price": {"id": "price_other"}}],
                                "has_more": False})),
    listing(subscription(id="sub_bad/id")),
    listing(subscription(status="incomplete")),
    b"not-json", b"x" * 262145, b"[]",
])
def test_ambiguous_incomplete_wrong_customer_wrong_price_never_verifies(payload):
    with pytest.raises(SubscriptionReconciliationError):
        discover_subscription(**KW, opener=transport(payload, []))

@pytest.mark.parametrize("changes", [
    {"tenant_id": "someone-else"},
    {"customer_id": "cus_bad/id"},
    {"price_id": "other"},
    {"api_key": "pk_test_" + "a" * 32},
    {"api_key": "sk_test_" + "a" * 20 + "\n"},
    {"api_key": "sk_test_" + "a" * 20 + "\u00e9"},
    {"timeout": True}, {"timeout": float("nan")}, {"timeout": 11},
])
def test_invalid_configuration_makes_no_stripe_request(changes):
    calls = []
    with pytest.raises(SubscriptionReconciliationError):
        discover_subscription(**{**KW, **changes},
            opener=transport(listing(subscription()), calls))
    assert calls == []

def test_live_credentials_cannot_use_test_mode_subscription():
    args = {**KW, "api_key": "sk_live_" + "a" * 32}
    with pytest.raises(SubscriptionReconciliationError):
        discover_subscription(**args, opener=transport(listing(subscription()), []))
    binding, _ = discover_subscription(**args,
        opener=transport(listing(subscription(livemode=True)), []))
    assert binding.subscription_id == "sub_valid"
