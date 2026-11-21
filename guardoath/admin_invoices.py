"""Provider-bound, read-only Stripe invoice history for the GuardOath owner."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import math
import re
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request

from guardoath.admin_billing import AdminSubscriptionReporter, BillingUnavailable
from guardoath.subscription_reconciliation import StripeBinding

INVOICE = re.compile(r"^in_[A-Za-z0-9_]{1,96}$")
CURRENCY = re.compile(r"^[a-z]{3}$")
STATUSES = frozenset({"draft", "open", "paid", "uncollectible", "void"})


class InvoiceHistoryUnavailable(ValueError):
    pass


def fetch_invoice_list(binding: StripeBinding, api_key: str, *, opener=None,
                       timeout: float = 5.0) -> dict:
    """Only server-owned IDs; bounded read, pinned Stripe API, no redirects."""
    if (not isinstance(binding, StripeBinding) or not isinstance(api_key, str)
            or not re.fullmatch(r"sk_(?:test|live)_[A-Za-z0-9_]{11,}", api_key)
            or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 10):
        raise InvoiceHistoryUnavailable("Invoice verification unavailable")
    url = "https://api.stripe.com/v1/invoices?" + urllib.parse.urlencode({
        "customer": binding.customer_id, "subscription": binding.subscription_id, "limit": 5})
    secret = base64.b64encode((api_key + ":").encode("ascii")).decode("ascii")
    request = urllib.request.Request(url, headers={
        "Authorization": "Basic " + secret, "Accept": "application/json",
        "Stripe-Version": "2025-02-24.acacia"})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    send = opener or urllib.request.build_opener(NoRedirect()).open
    try:
        with send(request, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                raise InvoiceHistoryUnavailable("Invoice provider unavailable")
            data = response.read(262145)
        if len(data) > 262144:
            raise InvoiceHistoryUnavailable("Oversized invoice response")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise InvoiceHistoryUnavailable("Invalid invoice response")
        return result
    except InvoiceHistoryUnavailable:
        raise
    except (OSError, ValueError, UnicodeError, urllib.error.HTTPError) as exc:
        raise InvoiceHistoryUnavailable("Invoice provider unavailable") from exc


class AdminInvoiceHistory:
    """Verify an existing Stripe subscription before reading its invoices.

    Neither an invoice status nor a payment amount authorizes paid access.
    """

    def __init__(self, fetch: Callable[[str, str, str], dict],
                 reporter: AdminSubscriptionReporter):
        if not callable(fetch) or not isinstance(reporter, AdminSubscriptionReporter):
            raise ValueError("Independent subscription reporter required")
        self.fetch, self.reporter = fetch, reporter

    def read(self, record) -> dict:
        try:
            verified = self.reporter.read(record)
        except BillingUnavailable as exc:
            raise InvoiceHistoryUnavailable("Subscription verification unavailable") from exc
        if verified.get("verified") is not True:
            return {"status": "unbound", "verified": False, "invoices": [],
                    "count": None, "has_more": None, "source": "unavailable: unverified subscription"}
        try:
            snapshot = self.fetch(record.tenant_id, record.payer_id, record.stripe_subscription_id)
        except Exception as exc:
            raise InvoiceHistoryUnavailable("Invoice provider unavailable") from exc
        if (not isinstance(snapshot, dict) or snapshot.get("object") != "list"
                or type(snapshot.get("has_more")) is not bool
                or not isinstance(snapshot.get("data"), list) or len(snapshot["data"]) > 5):
            raise InvoiceHistoryUnavailable("Incomplete invoice page")
        rows, seen = [], set()
        for item in snapshot["data"]:
            if not isinstance(item, dict):
                raise InvoiceHistoryUnavailable("Invalid invoice")
            invoice_id = item.get("id")
            customer, subscription = item.get("customer"), item.get("subscription")
            if isinstance(customer, dict):
                customer = customer.get("id")
            if isinstance(subscription, dict):
                subscription = subscription.get("id")
            values = [item.get(k) for k in ("amount_due", "amount_paid", "amount_remaining")]
            created = item.get("created")
            if (item.get("object") != "invoice" or not isinstance(invoice_id, str)
                    or not INVOICE.fullmatch(invoice_id) or invoice_id in seen
                    or customer != record.payer_id or subscription != record.stripe_subscription_id
                    or item.get("livemode") is not self.reporter.expected_livemode
                    or item.get("status") not in STATUSES
                    or not isinstance(item.get("currency"), str)
                    or not CURRENCY.fullmatch(item["currency"])
                    or any(type(v) is not int or v < 0 or v > 1_000_000_000_000 for v in values)
                    or type(created) is not int or not 946684800 <= created <= 4102444800):
                raise InvoiceHistoryUnavailable("Invoice binding or amount mismatch")
            seen.add(invoice_id)
            rows.append({"created_utc": datetime.fromtimestamp(created, timezone.utc)
                         .isoformat(timespec="seconds").replace("+00:00", "Z"),
                         "status": item["status"], "currency": item["currency"],
                         "amount_due_minor": values[0], "amount_paid_minor": values[1],
                         "amount_remaining_minor": values[2]})
        return {"status": "verified", "verified": True, "invoices": rows, "count": len(rows),
                "has_more": snapshot["has_more"], "read_at_utc":
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": "direct Stripe invoice list for verified subscription",
                "note": "Minor currency units, at most five invoices; not revenue or a forecast"}
