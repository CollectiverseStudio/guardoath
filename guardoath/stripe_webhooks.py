"""Strict Stripe webhook signature verification; no entitlement is granted here.

The authenticated billing reconciler must verify the provider's current subscription
state and tenant ownership before changing a tenant record.
"""
import hashlib
import hmac
import json
import time


class StripeWebhookError(ValueError):
    """Reject an untrusted, stale or malformed webhook without side effects."""


def verify_stripe_event(payload: bytes, signature: str, secret: str, *,
                        now: int | None = None, tolerance: int = 300) -> dict:
    """Verify Stripe-Signature against the exact raw request body.

    Returns a parsed event only after signature verification. Callers MUST NOT
    use the event as proof of entitlement without authoritative reconciliation.
    """
    if not isinstance(payload, bytes) or len(payload) > 262144:
        raise StripeWebhookError("Invalid webhook body")
    if not isinstance(secret, str) or not secret.startswith("whsec_") or len(secret) < 16:
        raise StripeWebhookError("Webhook secret is not configured")
    if not isinstance(signature, str) or len(signature) > 4096:
        raise StripeWebhookError("Invalid signature header")
    if type(tolerance) is not int or not 0 < tolerance <= 300:
        raise StripeWebhookError("Invalid timestamp tolerance")
    timestamps, signatures = [], []
    for part in signature.split(","):
        key, separator, value = part.strip().partition("=")
        if not separator:
            continue
        if key == "t" and value.isascii() and value.isdecimal() and len(value) <= 16:
            timestamps.append(int(value))
        elif key == "v1" and len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value):
            signatures.append(value.lower())
    if len(timestamps) != 1 or not signatures:
        raise StripeWebhookError("Missing valid signature fields")
    timestamp = timestamps[0]
    current = int(time.time()) if now is None else now
    if type(current) is not int or abs(current - timestamp) > tolerance:
        raise StripeWebhookError("Webhook timestamp outside tolerance")
    signed = str(timestamp).encode("ascii") + b"." + payload
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise StripeWebhookError("Webhook signature verification failed")
    try:
        event = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise StripeWebhookError("Invalid webhook JSON") from exc
    if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not event["id"].startswith("evt_") or not isinstance(event.get("type"), str) or not isinstance(event.get("data"), dict) or not isinstance(event["data"].get("object"), dict):
        raise StripeWebhookError("Invalid webhook event structure")
    return event
