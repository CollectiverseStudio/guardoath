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


def fetch_subscription_snapshot(binding: StripeBinding, api_key: str, *,
                                opener=None, timeout: float = 5.0) -> dict:
    """Return a directly fetched Stripe snapshot, never client-reported state."""
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
    # Validate the authoritative binding before a caller may persist the snapshot.
    reconcile_subscription(binding, subscription)
    return subscription


def fetch_subscription(binding: StripeBinding, api_key: str, *,
                       opener=None, timeout: float = 5.0) -> EntitlementDecision:
    return reconcile_subscription(binding, fetch_subscription_snapshot(
        binding, api_key, opener=opener, timeout=timeout))


def discover_subscription(*, tenant_id: str, customer_id: str, price_id: str,
                          api_key: str, opener=None, timeout: float = 5.0
                          ) -> tuple[StripeBinding, dict]:
    """Discover one existing customer subscription with the configured price.

    Read-only Stripe list request; never accept a subscription or a price from
    the browser. An ambiguous, truncated, malformed or missing result fails
    closed, rather than activating the first matching subscription.
    """
    import math
    if (not isinstance(tenant_id, str) or not tenant_id.startswith("usr-")
            or len(tenant_id) > 40 or not isinstance(customer_id, str)
            or not customer_id.startswith("cus_")
            or not customer_id[4:].replace("_", "").isalnum()
            or len(customer_id) > 100
            or not isinstance(price_id, str) or not price_id.startswith("price_")
            or not price_id[6:].replace("_", "").isalnum()
            or len(price_id) > 100
            or not isinstance(api_key, str)
            or not api_key.startswith(("sk_test_", "sk_live_"))
            or len(api_key) < 16 or not api_key.isascii()
            or any(c.isspace() for c in api_key)
            or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 10):
        raise SubscriptionReconciliationError("Stripe discovery configuration unavailable")
    params = urllib.parse.urlencode({
        "customer": customer_id, "status": "all", "limit": "100"
    })
    request = urllib.request.Request(
        "https://api.stripe.com/v1/subscriptions?" + params,
        headers={"Authorization": "Basic " +
            base64.b64encode((api_key + ":").encode("ascii")).decode("ascii"),
            "Accept": "application/json", "Stripe-Version": "2025-02-24.acacia"},
    )
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    send = opener or urllib.request.build_opener(NoRedirect()).open
    try:
        with send(request, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                raise SubscriptionReconciliationError("Stripe discovery failed")
            raw = response.read(262145)
        if len(raw) > 262144:
            raise SubscriptionReconciliationError("Oversized Stripe subscription list")
        result = json.loads(raw)
    except (OSError, ValueError, UnicodeError, urllib.error.HTTPError) as exc:
        raise SubscriptionReconciliationError("Stripe discovery unavailable") from exc
    if (not isinstance(result, dict) or result.get("object") != "list"
            or result.get("has_more") is not False
            or not isinstance(result.get("data"), list)
            or len(result["data"]) > 100):
        raise SubscriptionReconciliationError("Incomplete Stripe subscription list")
    matching = []
    for subscription in result["data"]:
        if not isinstance(subscription, dict) or subscription.get("object") != "subscription":
            raise SubscriptionReconciliationError("Invalid Stripe subscription entry")
        customer = subscription.get("customer")
        if isinstance(customer, dict):
            customer = customer.get("id")
        if customer != customer_id:
            raise SubscriptionReconciliationError("Cross-customer subscription entry")
        if subscription.get("livemode") is not api_key.startswith("sk_live_"):
            raise SubscriptionReconciliationError("Stripe subscription environment mismatch")
        items = subscription.get("items")
        entries = items.get("data") if isinstance(items, dict) else None
        if not isinstance(entries, list) or items.get("has_more") is not False:
            raise SubscriptionReconciliationError("Incomplete subscription prices")
        found_price = False
        for item in entries:
            price = item.get("price") if isinstance(item, dict) else None
            if isinstance(price, dict) and price.get("id") == price_id:
                found_price = True
            elif isinstance(price, str) and price == price_id:
                found_price = True
        if found_price and subscription.get("status") in {"active", "past_due", "unpaid", "canceled"}:
            sub_id = subscription.get("id")
            if (not isinstance(sub_id, str) or not sub_id.startswith("sub_")
                    or not sub_id[4:].replace("_", "").isalnum()
                    or len(sub_id) > 100):
                raise SubscriptionReconciliationError("Invalid subscription identifier")
            matching.append((StripeBinding(tenant_id, customer_id, sub_id), subscription))
    if len(matching) != 1:
        raise SubscriptionReconciliationError("No unique matching Stripe subscription")
    return matching[0]



def verified_subscription_snapshot(*, tenant_id: str, customer_id: str,
                                   price_id: str, api_key: str, opener=None,
                                   timeout: float = 5.0) -> tuple[StripeBinding, dict]:
    """Return only a directly fetched provider snapshot with current trusted price."""
    binding, list_snapshot = discover_subscription(
        tenant_id=tenant_id, customer_id=customer_id, price_id=price_id,
        api_key=api_key, opener=opener, timeout=timeout)
    direct = fetch_subscription_snapshot(binding, api_key, opener=opener, timeout=timeout)
    if (reconcile_subscription(binding, direct).status !=
            reconcile_subscription(binding, list_snapshot).status):
        raise SubscriptionReconciliationError("Subscription changed during verification")
    # A subscription can switch to a different product/price between the list
    # and GET. An authenticated customer must never receive the wrong plan.
    items = direct.get("items")
    entries = items.get("data") if isinstance(items, dict) else None
    if (not isinstance(entries, list)
            or items.get("has_more") is not False
            or len(entries) != 1
            or not isinstance(entries[0], dict)):
        raise SubscriptionReconciliationError("Invalid current subscription items")
    price = entries[0].get("price")
    actual_price = price.get("id") if isinstance(price, dict) else price
    if actual_price != price_id:
        raise SubscriptionReconciliationError("Subscription price changed")
    return binding, direct


def verify_discovered_subscription(*, tenant_id: str, customer_id: str,
                                   price_id: str, api_key: str, opener=None,
                                   timeout: float = 5.0) -> EntitlementDecision:
    """Two independent Stripe reads, correct customer, price and status."""
    binding, snapshot = verified_subscription_snapshot(
        tenant_id=tenant_id, customer_id=customer_id, price_id=price_id,
        api_key=api_key, opener=opener, timeout=timeout)
    return reconcile_subscription(binding, snapshot)
