"""Offline regressions for strict Stripe JSON and two-source environment binding."""
import json

import pytest

from guardoath.stripe_provider import (
    fetch_subscription_snapshot, discover_subscription, verify_discovered_subscription,
)
from guardoath.subscription_reconciliation import StripeBinding, SubscriptionReconciliationError

KEY = "sk_test_" + "a" * 32
BINDING = StripeBinding("usr-acme", "cus_acme", "sub_acme")
KW = dict(tenant_id="usr-acme", customer_id="cus_acme",
          price_id="price_starter", api_key=KEY)


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


def valid(**changes):
    return {"object": "subscription", "id": "sub_acme", "customer": "cus_acme",
            "status": "active", "livemode": False,
            "pause_collection": None, "collection_method": "charge_automatically",
            "items": {"data": [{"price": {"id": "price_starter"}, "quantity": 1}], "has_more": False},
            **changes}


def page(doc):
    return b'{"object":"list","has_more":false,"data":[' + doc + b']}'


@pytest.mark.parametrize("tail", [
    b',"status":"active"', b',"metadata":{"x":0,"x":1}',
    b',"metadata":{"x":NaN}', b',"metadata":{"x":Infinity}',
    b',"metadata":{"x":-Infinity}', b',"metadata":{"x":1e999}',
])
def test_both_fetch_and_list_reject_ambiguous_json(tail):
    malformed = json.dumps(valid()).encode()[:-1] + tail + b'}'
    with pytest.raises(SubscriptionReconciliationError):
        fetch_subscription_snapshot(BINDING, KEY,
                                    opener=lambda *_a, **_kw: Response(malformed))
    with pytest.raises(SubscriptionReconciliationError):
        discover_subscription(**KW, opener=lambda *_a, **_kw: Response(page(malformed)))


@pytest.mark.parametrize("mode", [None, True, 0, "false"])
def test_direct_mode_fails_closed(mode):
    calls = []

    def open(request, timeout):
        calls.append(request.full_url)
        return Response(page(json.dumps(valid()).encode()) if "?" in request.full_url
                        else json.dumps(valid(livemode=mode)).encode())

    with pytest.raises(SubscriptionReconciliationError):
        verify_discovered_subscription(**KW, opener=open)
    assert len(calls) == 2


@pytest.mark.parametrize("state", [None, True, [], {}])
def test_bad_discovery_status_fails_closed(state):
    with pytest.raises(SubscriptionReconciliationError):
        discover_subscription(**KW, opener=lambda *_a, **_kw: Response(
            page(json.dumps(valid(status=state)).encode())))


def test_finite_values_and_matching_mode_succeed():
    data = json.dumps(valid(metadata={"ordinary_decimal": 1.25})).encode()
    result = verify_discovered_subscription(
        **KW, opener=lambda request, timeout: Response(
            page(data) if "?" in request.full_url else data))
    assert result.active
