"""Provider-bound, read-only Stripe invoice history for the GuardOath owner."""
from __future__ import annotations

import base64
import copy
from collections import deque
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from threading import Lock
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from guardoath.admin_billing import AdminSubscriptionReporter, BillingUnavailable
from guardoath.subscription_reconciliation import StripeBinding

INVOICE = re.compile(r"^in_[A-Za-z0-9_]{1,96}$")
CURRENCY = re.compile(r"^[a-z]{3}$")
STATUSES = frozenset({"draft", "open", "paid", "uncollectible", "void"})
PAGE_SIZE = 5
CURSOR_TTL = 900
_CURSOR_PURPOSE = b"guardoath-owner-invoice-cursor-v1"


class InvalidInvoiceCursor(ValueError):
    """A cursor/filter is invalid for this owner and saved billing binding."""


class InvoiceHistoryUnavailable(ValueError):
    pass


class InvoiceReadRateLimited(InvoiceHistoryUnavailable):
    pass


def _unique_provider_keys(pairs):
    """Stripe JSON objects must never contain competing values for one key."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvoiceHistoryUnavailable("Ambiguous invoice provider JSON")
        result[key] = value
    return result


def _reject_nonfinite_constant(value):
    raise InvoiceHistoryUnavailable("Nonfinite invoice provider value")


def _finite_provider_float(value):
    """Allow ordinary unrelated Stripe decimals, reject numeric overflow."""
    parsed = float(value)
    if not math.isfinite(parsed):
        raise InvoiceHistoryUnavailable("Nonfinite invoice provider value")
    return parsed


def fetch_invoice_list(binding: StripeBinding, api_key: str, *, opener=None,
                       timeout: float = 5.0, starting_after: str | None = None,
                       status: str | None = None) -> dict:
    """Only server-owned IDs; bounded read, pinned Stripe API, no redirects."""
    if (not isinstance(binding, StripeBinding) or not isinstance(api_key, str)
            or not re.fullmatch(r"sk_(?:test|live)_[A-Za-z0-9_]{11,}", api_key)
            or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 10
            or (starting_after is not None and
                (not isinstance(starting_after, str) or not INVOICE.fullmatch(starting_after)))
            or (status is not None and (not isinstance(status, str) or status not in STATUSES))):
        raise InvoiceHistoryUnavailable("Invoice verification unavailable")
    params = {"customer": binding.customer_id, "subscription": binding.subscription_id,
              "limit": PAGE_SIZE}
    if starting_after is not None:
        params["starting_after"] = starting_after
    if status is not None:
        params["status"] = status
    url = "https://api.stripe.com/v1/invoices?" + urllib.parse.urlencode(params)
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
        result = json.loads(data, object_pairs_hook=_unique_provider_keys,
                            parse_constant=_reject_nonfinite_constant,
                            parse_float=_finite_provider_float)
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

    def __init__(self, fetch: Callable[..., dict],
                 reporter: AdminSubscriptionReporter, *, cursor_key: bytes,
                 clock: Callable[[], float] = time.time):
        if (not callable(fetch) or not isinstance(reporter, AdminSubscriptionReporter)
                or not isinstance(cursor_key, bytes) or len(cursor_key) < 32
                or not callable(clock)):
            raise ValueError("Independent subscription reporter required")
        self.fetch, self.reporter = fetch, reporter
        self._cipher = AESGCM(hmac.new(cursor_key, _CURSOR_PURPOSE, hashlib.sha256).digest())
        self.clock = clock
        self._read_lock = Lock()
        self._read_times: dict[str, deque] = {}

    def _reserve_read(self, owner_sub: str, now: int) -> None:
        # Best-effort per-warm-instance budget, not a distributed rate limit.
        # Invalid cursors are rejected before this window and any provider read.
        with self._read_lock:
            if owner_sub not in self._read_times and len(self._read_times) >= 128:
                self._read_times = {owner: times for owner, times in self._read_times.items()
                                    if times and times[-1] > now - 60}
                if len(self._read_times) >= 128:
                    raise InvoiceReadRateLimited("Invoice reads temporarily limited")
            times = self._read_times.setdefault(owner_sub, deque())
            while times and times[0] <= now - 60:
                times.popleft()
            if len(times) >= 20:
                raise InvoiceReadRateLimited("Invoice reads temporarily limited")
            times.append(now)

    def _now(self) -> int:
        value = self.clock()
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 946684800 <= value <= 4102444800):
            raise InvoiceHistoryUnavailable("Invoice read clock unavailable")
        return int(value)

    def _context(self, record, owner_sub: str, status: str | None) -> bytes:
        if (not isinstance(owner_sub, str)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", owner_sub)
                or (status is not None and (not isinstance(status, str) or status not in STATUSES))):
            raise InvalidInvoiceCursor("Invalid invoice owner or filter")
        values = [getattr(record, name, None) for name in
                  ("tenant_id", "payer_id", "stripe_subscription_id")]
        if (not isinstance(values[0], str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", values[0])
                or any(value is not None and (not isinstance(value, str) or len(value) > 100)
                       for value in values[1:])):
            raise InvoiceHistoryUnavailable("Invoice binding unavailable")
        # Authenticated encryption hides the provider invoice ID. Associated
        # data binds it to the owner and freshly loaded billing authority, not
        # to a tenant revision that normal request accounting may advance.
        return _CURSOR_PURPOSE + json.dumps({
            "owner": owner_sub, "tenant": values[0], "customer": values[1],
            "subscription": values[2], "price": self.reporter.expected_price_id,
            "livemode": self.reporter.expected_livemode, "status": status,
            "limit": PAGE_SIZE}, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _open_cursor(self, cursor: str, context: bytes, now: int) -> dict:
        if (not isinstance(cursor, str) or not re.fullmatch(r"ic1\.[A-Za-z0-9_-]{40,2044}", cursor)):
            raise InvalidInvoiceCursor("Invalid or expired invoice cursor")
        try:
            encoded = cursor[4:]
            sealed = base64.b64decode(encoded + "=" * (-len(encoded) % 4),
                                      altchars=b"-_", validate=True)
            if (not 28 <= len(sealed) <= 1500 or
                    base64.urlsafe_b64encode(sealed).rstrip(b"=").decode("ascii") != encoded):
                raise ValueError("Noncanonical cursor")
            payload = json.loads(self._cipher.decrypt(sealed[:12], sealed[12:], context))
            if (not isinstance(payload, dict) or set(payload) != {"v", "after", "created", "page", "exp"}
                    or type(payload["v"]) is not int or payload["v"] != 1
                    or not isinstance(payload["after"], str) or not INVOICE.fullmatch(payload["after"])
                    or type(payload["created"]) is not int or not 946684800 <= payload["created"] <= 4102444800
                    or type(payload["page"]) is not int or not 2 <= payload["page"] <= 10000
                    or type(payload["exp"]) is not int or not now < payload["exp"] <= now + CURSOR_TTL):
                raise ValueError("Invalid cursor payload")
            return payload
        except (InvalidTag, ValueError, TypeError, UnicodeError, OverflowError) as exc:
            raise InvalidInvoiceCursor("Invalid or expired invoice cursor") from exc

    def _seal_cursor(self, *, invoice_id: str, created: int, page: int,
                     expires: int, context: bytes) -> str:
        if page > 10000 or expires <= self._now():
            raise InvoiceHistoryUnavailable("Invoice traversal expired; restart from the first page")
        data = json.dumps({"v": 1, "after": invoice_id, "created": created,
                           "page": page, "exp": expires},
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
        nonce = secrets.token_bytes(12)
        sealed = nonce + self._cipher.encrypt(nonce, data, context)
        return "ic1." + base64.urlsafe_b64encode(sealed).rstrip(b"=").decode("ascii")

    def read(self, record, *, owner_sub: str, cursor: str | None = None,
             status: str | None = None) -> dict:
        # Read and verify one immutable saved binding for the entire request.
        record = copy.deepcopy(record)
        now = self._now()
        context = self._context(record, owner_sub, status)
        continuation = self._open_cursor(cursor, context, now) if cursor is not None else None
        page = continuation["page"] if continuation else 1
        expires = continuation["exp"] if continuation else now + CURSOR_TTL
        self._reserve_read(owner_sub, now)
        try:
            verified = self.reporter.read(record)
        except BillingUnavailable as exc:
            raise InvoiceHistoryUnavailable("Subscription verification unavailable") from exc
        if verified.get("verified") is not True:
            if continuation is not None:
                raise InvalidInvoiceCursor("Invoice binding no longer available")
            return {"status": "unbound", "verified": False, "invoices": [],
                    "count": None, "has_more": None, "payment_review": None,
                    "page_number": 1, "status_filter": status, "next_cursor": None,
                    "source": "unavailable: unverified subscription"}
        try:
            snapshot = self.fetch(record.tenant_id, record.payer_id, record.stripe_subscription_id,
                                  starting_after=continuation["after"] if continuation else None,
                                  status=status)
        except Exception as exc:
            raise InvoiceHistoryUnavailable("Invoice provider unavailable") from exc
        if (not isinstance(snapshot, dict) or snapshot.get("object") != "list"
                or type(snapshot.get("has_more")) is not bool
                or not isinstance(snapshot.get("data"), list) or len(snapshot["data"]) > PAGE_SIZE
                or (snapshot["has_more"] and not snapshot["data"])):
            raise InvoiceHistoryUnavailable("Incomplete invoice page")
        rows, seen = [], set()
        # Bounded page-review signals, not full accounts receivable.
        open_balances, open_count, uncollectible_count, recorded_retry_count = {}, 0, 0, 0
        previous_created = continuation["created"] if continuation else 4102444800
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
            invoice_status = item.get("status")
            collection_method = item.get("collection_method")
            attempted = item.get("attempted")
            attempt_count = item.get("attempt_count")
            next_attempt = item.get("next_payment_attempt")
            if (item.get("object") != "invoice" or not isinstance(invoice_id, str)
                    or not INVOICE.fullmatch(invoice_id) or invoice_id in seen
                    or customer != record.payer_id or subscription != record.stripe_subscription_id
                    or item.get("livemode") is not self.reporter.expected_livemode
                    or not isinstance(invoice_status, str) or invoice_status not in STATUSES
                    or (status is not None and invoice_status != status)
                    or (continuation is not None and invoice_id == continuation["after"])
                    or (collection_method is not None and collection_method not in
                        ("charge_automatically", "send_invoice"))
                    or (attempted is not None and type(attempted) is not bool)
                    or (attempt_count is not None and
                        (type(attempt_count) is not int or not 0 <= attempt_count <= 10000))
                    or (next_attempt is not None and
                        (type(next_attempt) is not int or
                         not 946684800 <= next_attempt <= 4102444800))
                    or not isinstance(item.get("currency"), str)
                    or not CURRENCY.fullmatch(item["currency"])
                    or any(type(v) is not int or v < 0 or v > 1_000_000_000_000 for v in values)
                    or type(created) is not int or not 946684800 <= created <= previous_created):
                raise InvoiceHistoryUnavailable("Invoice binding or amount mismatch")
            previous_created = created
            seen.add(invoice_id)
            rows.append({"created_utc": datetime.fromtimestamp(created, timezone.utc)
                         .isoformat(timespec="seconds").replace("+00:00", "Z"),
                         "status": item["status"], "currency": item["currency"],
                         "amount_due_minor": values[0], "amount_paid_minor": values[1],
                         "amount_remaining_minor": values[2],
                         "collection_method": collection_method,
                         "attempted": attempted, "attempt_count": attempt_count,
                         "next_payment_attempt_utc": (
                             datetime.fromtimestamp(next_attempt, timezone.utc)
                             .isoformat(timespec="seconds").replace("+00:00", "Z")
                             if next_attempt is not None else None)})
            if item["status"] == "open" and values[2] > 0:
                open_count += 1
                currency = item["currency"]
                open_balances[currency] = open_balances.get(currency, 0) + values[2]
                # Recorded next attempt is NOT proof of a failed payment or future retry.
                if (collection_method == "charge_automatically"
                        and attempted is True and attempt_count is not None
                        and attempt_count > 0 and next_attempt is not None):
                    recorded_retry_count += 1
            if item["status"] == "uncollectible":
                uncollectible_count += 1
        next_cursor = (self._seal_cursor(invoice_id=snapshot["data"][-1]["id"],
                       created=previous_created, page=page + 1, expires=expires, context=context)
                       if snapshot["has_more"] else None)
        first_unfiltered = continuation is None and status is None
        return {"status": "verified", "verified": True, "invoices": rows, "count": len(rows),
                "has_more": snapshot["has_more"],
                "page_number": page, "status_filter": status, "next_cursor": next_cursor,
                "payment_review": {
                    "scope": "verified_first_page_only" if first_unfiltered else "verified_selected_page_only",
                    "open_with_balance_count": open_count,
                    "open_with_recorded_next_attempt_count": recorded_retry_count,
                    "uncollectible_count": uncollectible_count,
                    "open_balance_by_currency_minor": dict(sorted(open_balances.items())),
                    "complete_history": first_unfiltered and not snapshot["has_more"],
                    "note": "Next-attempt timestamps and open balances do not prove failed payment, "
                            "overdue debt or a future retry; uncollectible is a provider status, "
                            "not a refund or owner action",
                },
                "read_at_utc":
                datetime.fromtimestamp(self._now(), timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "source": "direct Stripe invoice list for verified subscription",
                "note": "Minor currency units, at most five invoices per page. Pages are separate "
                        "provider observations, not a financial snapshot or accumulated total; recorded "
                        "collection attempts are not proof of failed payment, overdue debt or future charges"}
