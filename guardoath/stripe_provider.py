"""Server-side Stripe subscription retrieval for trusted entitlement reconciliation.

No user-controlled Stripe URL or subscription ID is accepted. Call this only
after loading a server-owned StripeBinding from persistent tenant billing state.
"""
import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from guardoath.subscription_reconciliation import (
    EntitlementDecision, StripeBinding, SubscriptionReconciliationError,
    reconcile_subscription,
)


def fetch_subscription(binding: StripeBinding, api_key: str, *,
                       opener=None, timeout: float = 5.0) -> EntitlementDecision:
    """Retrieve Stripe's authoritative current state; fail closed on all errors."""
    if (not isinstance(api_key, str)
            or not api_key.startswith(("sk_test_", "sk_live_"))
            or len(api_key) < 16 or not api_key.isascii()
            or any(character.isspace() for character in api_key)):
        raise SubscriptionReconciliationError("Stripe API credential is not configured")
    # A bool is an int in Python, but True is not a deliberate 1s timeout.
    # NaN and infinity are also invalid, without accepting non-numeric values.
    import math
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 10):
        raise SubscriptionReconciliationError("Invalid Stripe request timeout")
    # Stripe IDs are server-owned, but still validate before embedding in URL.
    subscription_id = binding.subscription_id
    if not subscription_id.startswith("sub_") or not subscription_id[4:].replace("_", "").isalnum() or len(subscription_id) > 100:
        raise SubscriptionReconciliationError("Invalid subscription identifier")
    url = "https://api.stripe.com/v1/subscriptions/" + urllib.parse.quote(subscription_id, safe="")
    basic = base64.b64encode((api_key + ":").encode("ascii")).decode("ascii")
    request = urllib.request.Request(url, headers={
        "Authorization": "Basic " + basic,
        "Accept": "application/json",
        "Stripe-Version": "2025-02-24.acacia",
    })
    # Do not follow redirects: Stripe credentials must never be forwarded to
    # another host, including a provider-supplied Location header.
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    send = opener or urllib.request.build_opener(_NoRedirect()).open
    try:
        with send(request, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                raise SubscriptionReconciliationError("Stripe subscription lookup failed")
            payload = response.read(262145)
        if len(payload) > 262144:
            raise SubscriptionReconciliationError("Oversized Stripe response")
        subscription = json.loads(payload)
    except (OSError, ValueError, UnicodeError, urllib.error.HTTPError) as exc:
        raise SubscriptionReconciliationError("Unable to verify Stripe subscription") from exc
    return reconcile_subscription(binding, subscription)
