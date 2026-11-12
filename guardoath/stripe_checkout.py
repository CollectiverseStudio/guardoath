"""Server-only Stripe Checkout session creation for a registered tenant.

The caller must authenticate the owner and load the Stripe customer binding
from the authoritative tenant record. A checkout redirect is not proof of payment.
"""
import base64
import json
import urllib.parse
import urllib.request

from guardoath.subscription_reconciliation import SubscriptionReconciliationError


class StripeCheckoutError(ValueError):
    pass


def create_checkout_session(*, customer_id: str, price_id: str, tenant_id: str,
                            api_key: str, success_url: str, cancel_url: str,
                            idempotency_key: str, opener=None) -> str:
    """Return a verified Stripe-hosted checkout URL, never an entitlement."""
    if not isinstance(customer_id, str) or not customer_id.startswith("cus_") or not customer_id[4:].replace("_", "").isalnum():
        raise StripeCheckoutError("Invalid Stripe customer")
    if not isinstance(price_id, str) or not price_id.startswith("price_") or not price_id[6:].replace("_", "").isalnum():
        raise StripeCheckoutError("Invalid server-configured price")
    if not isinstance(tenant_id, str) or not tenant_id or len(tenant_id) > 40:
        raise StripeCheckoutError("Invalid tenant")
    if not isinstance(api_key, str) or not api_key.startswith(("sk_test_", "sk_live_")) or not api_key.isascii() or len(api_key) < 16:
        raise StripeCheckoutError("Stripe credential unavailable")
    for url in (success_url, cancel_url):
        parsed = urllib.parse.urlsplit(url) if isinstance(url, str) else None
        if parsed is None or parsed.scheme != "https" or parsed.netloc != "guardoath.com" or parsed.username or parsed.password or parsed.fragment:
            raise StripeCheckoutError("Checkout return URLs must use guardoath.com")
    if not isinstance(idempotency_key, str) or not 12 <= len(idempotency_key) <= 200 or not idempotency_key.isascii() or any(c.isspace() for c in idempotency_key):
        raise StripeCheckoutError("Invalid idempotency key")
    data = urllib.parse.urlencode({
        "mode": "subscription", "customer": customer_id,
        "line_items[0][price]": price_id, "line_items[0][quantity]": "1",
        "client_reference_id": tenant_id,
        "success_url": success_url, "cancel_url": cancel_url,
    }).encode("ascii")
    token = base64.b64encode((api_key + ":").encode("ascii")).decode("ascii")
    request = urllib.request.Request("https://api.stripe.com/v1/checkout/sessions",
        data=data, method="POST", headers={
            "Authorization": "Basic " + token,
            "Content-Type": "application/x-www-form-urlencoded",
            "Idempotency-Key": idempotency_key,
        })
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    send = opener or urllib.request.build_opener(NoRedirect()).open
    try:
        with send(request, timeout=5) as response:
            if getattr(response, "status", 200) != 200:
                raise StripeCheckoutError("Checkout provider rejected request")
            raw = response.read(65537)
        if len(raw) > 65536:
            raise StripeCheckoutError("Oversized checkout response")
        result = json.loads(raw)
    except (OSError, UnicodeError, ValueError) as exc:
        raise StripeCheckoutError("Checkout provider unavailable") from exc
    # A provider redirect is not permission to activate subscription. Bind the
    # returned session to exactly the server-selected tenant and configured
    # Stripe mode before disclosing a checkout destination to the customer.
    # The provider always supplies these fields for a created Checkout Session.
    if (not isinstance(result, dict)
            or result.get("object") != "checkout.session"
            or not isinstance(result.get("id"), str)
            or not result["id"].startswith("cs_")
            or result.get("customer") != customer_id
            or result.get("client_reference_id") != tenant_id
            or result.get("mode") != "subscription"
            or result.get("status") != "open"
            or result.get("livemode") is not api_key.startswith("sk_live_")):
        raise StripeCheckoutError("Invalid checkout provider response")
    url = result.get("url")
    if not isinstance(url, str) or not 1 <= len(url) <= 4096:
        raise StripeCheckoutError("Unexpected checkout destination")
    try:
        parsed = urllib.parse.urlsplit(url)
        safe_destination = (parsed.scheme == "https" and
            parsed.hostname == "checkout.stripe.com" and
            parsed.username is None and parsed.password is None and
            parsed.port in (None, 443) and not parsed.fragment)
    except ValueError:
        safe_destination = False
    if not safe_destination:
        raise StripeCheckoutError("Unexpected checkout destination")
    return url
