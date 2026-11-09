"""Server-side Stripe customer provisioning, without granting paid entitlement."""
import base64
import json
import urllib.parse
import urllib.request


class StripeCustomerError(ValueError):
    pass


def create_customer(*, tenant_id: str, api_key: str, idempotency_key: str, opener=None) -> str:
    if not isinstance(tenant_id, str) or not tenant_id.startswith("usr-") or len(tenant_id) > 40 or not all(c.isascii() and (c.isalnum() or c == "-") for c in tenant_id):
        raise StripeCustomerError("Invalid customer tenant")
    if not isinstance(api_key, str) or not api_key.startswith(("sk_test_", "sk_live_")) or len(api_key) < 16 or not api_key.isascii():
        raise StripeCustomerError("Stripe credential unavailable")
    if not isinstance(idempotency_key, str) or not 12 <= len(idempotency_key) <= 200 or not idempotency_key.isascii() or any(c.isspace() for c in idempotency_key):
        raise StripeCustomerError("Invalid idempotency key")
    body = urllib.parse.urlencode({"metadata[guardoath_tenant_id]": tenant_id}).encode()
    basic = base64.b64encode((api_key + ":").encode()).decode()
    request = urllib.request.Request("https://api.stripe.com/v1/customers", data=body,
        method="POST", headers={"Authorization": "Basic " + basic,
                                "Content-Type": "application/x-www-form-urlencoded",
                                "Idempotency-Key": idempotency_key})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    send = opener or urllib.request.build_opener(NoRedirect()).open
    try:
        with send(request, timeout=5) as response:
            if getattr(response, "status", 200) != 200:
                raise StripeCustomerError("Customer creation failed")
            raw = response.read(65537)
        if len(raw) > 65536:
            raise StripeCustomerError("Oversized customer response")
        customer = json.loads(raw)
    except (OSError, UnicodeError, ValueError) as exc:
        raise StripeCustomerError("Customer provisioning unavailable") from exc
    if not isinstance(customer, dict) or customer.get("object") != "customer":
        raise StripeCustomerError("Invalid customer response")
    customer_id = customer.get("id")
    if not isinstance(customer_id, str) or not customer_id.startswith("cus_") or not customer_id[4:].replace("_", "").isalnum() or len(customer_id) > 100:
        raise StripeCustomerError("Invalid Stripe customer ID")
    if customer.get("metadata", {}).get("guardoath_tenant_id") != tenant_id:
        raise StripeCustomerError("Stripe customer tenant binding mismatch")
    return customer_id
