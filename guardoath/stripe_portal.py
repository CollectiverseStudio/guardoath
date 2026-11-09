"""Create a Stripe Billing Portal session using a server-owned customer binding.

This returns a provider-hosted URL only; billing changes must be reconciled
separately before altering tenant entitlement.
"""
import base64
import json
import urllib.parse
import urllib.request


class StripePortalError(ValueError):
    pass


def create_portal_session(*, customer_id: str, api_key: str,
                          return_url: str = "https://guardoath.com/dashboard",
                          opener=None) -> str:
    if not isinstance(customer_id, str) or not customer_id.startswith("cus_") or not customer_id[4:].replace("_", "").isalnum() or len(customer_id) > 100:
        raise StripePortalError("Invalid server-owned customer ID")
    if not isinstance(api_key, str) or not api_key.startswith(("sk_test_", "sk_live_")) or not api_key.isascii() or len(api_key) < 16:
        raise StripePortalError("Stripe API credential unavailable")
    if return_url != "https://guardoath.com/dashboard":
        raise StripePortalError("Unapproved billing portal return URL")
    body = urllib.parse.urlencode({"customer": customer_id, "return_url": return_url}).encode()
    token = base64.b64encode((api_key + ":").encode()).decode()
    request = urllib.request.Request("https://api.stripe.com/v1/billing_portal/sessions",
        data=body, method="POST", headers={
            "Authorization": "Basic " + token,
            "Content-Type": "application/x-www-form-urlencoded",
        })
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    send = opener or urllib.request.build_opener(NoRedirect()).open
    try:
        with send(request, timeout=5) as response:
            if getattr(response, "status", 200) != 200:
                raise StripePortalError("Stripe portal request failed")
            raw = response.read(65537)
        if len(raw) > 65536:
            raise StripePortalError("Oversized Stripe portal response")
        data = json.loads(raw)
    except (OSError, UnicodeError, ValueError) as exc:
        raise StripePortalError("Stripe portal unavailable") from exc
    if not isinstance(data, dict) or data.get("object") != "billing_portal.session":
        raise StripePortalError("Invalid Stripe portal response")
    url = data.get("url")
    try:
        parsed = urllib.parse.urlsplit(url) if isinstance(url, str) else None
        valid = (parsed is not None and parsed.scheme == "https" and
                 parsed.hostname == "billing.stripe.com" and
                 parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except ValueError:
        valid = False
    if not valid:
        raise StripePortalError("Unexpected billing portal destination")
    return url
