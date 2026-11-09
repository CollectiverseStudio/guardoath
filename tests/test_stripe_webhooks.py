"""Adversarial Stripe signature contract; no network or real Stripe account required."""
import hashlib
import hmac
import json

import pytest

from guardoath.stripe_webhooks import StripeWebhookError, verify_stripe_event

SECRET = "whsec_" + "s" * 32
NOW = 2_000_000_000
EVENT = {"id": "evt_test_123", "type": "customer.subscription.updated",
         "data": {"object": {"id": "sub_123", "customer": "cus_123", "status": "active"}}}
BODY = json.dumps(EVENT, separators=(",", ":")).encode()


def header(body=BODY, timestamp=NOW, secret=SECRET):
    mac = hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def test_valid_event_and_multiple_signatures():
    assert verify_stripe_event(BODY, header(), SECRET, now=NOW) == EVENT
    assert verify_stripe_event(BODY, header() + ",v1=" + "0" * 64, SECRET, now=NOW) == EVENT


@pytest.mark.parametrize("body,sig,clock", [
    (BODY + b" ", header(), NOW),
    (BODY, header(secret="whsec_" + "x" * 32), NOW),
    (BODY, header(), NOW + 301),
    (BODY, header(), NOW - 301),
    (BODY, "t=2000000000,v1=" + "0" * 64, NOW),
    (BODY, "t=2000000000,t=2000000000," + header().split(",", 1)[1], NOW),
    (BODY, "t=not-a-number," + header().split(",", 1)[1], NOW),
    (b"x" * 262145, header(), NOW),
])
def test_tampering_replay_and_oversize_are_rejected(body, sig, clock):
    with pytest.raises(StripeWebhookError):
        verify_stripe_event(body, sig, SECRET, now=clock)


def test_signed_malformed_event_cannot_pass():
    for payload in (b"not json", b"[]", b"{}", b'{"id":"evt_a","type":"test","data":{}}'):
        with pytest.raises(StripeWebhookError):
            verify_stripe_event(payload, header(body=payload), SECRET, now=NOW)


def test_missing_secret_is_not_accepted():
    with pytest.raises(StripeWebhookError):
        verify_stripe_event(BODY, header(), "", now=NOW)
