"""Tenant-scoped owner inspection of recent Stripe subscription and invoice events."""

import base64
import hmac
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from guardoath.admin_billing import BillingUnavailable
from guardoath.admin_cancellation_provider import provider_id
from guardoath.stripe_provider import _strict_provider_json

EVENT_LABELS = {
    "customer.subscription.created": "Subscription created",
    "customer.subscription.updated": "Subscription updated",
    "customer.subscription.deleted": "Subscription ended",
    "customer.subscription.paused": "Subscription paused",
    "customer.subscription.resumed": "Subscription resumed",
    "invoice.payment_failed": "Invoice payment failed",
    "invoice.payment_action_required": "Customer action required for payment",
    "invoice.payment_succeeded": "Invoice payment succeeded",
    "invoice.marked_uncollectible": "Invoice marked uncollectible",
}
WINDOW = 7 * 86400
CURSOR_TTL = 900
PAGE_SIZE = 10


class EventCursorError(ValueError):
    """Restart the owner's selected event window."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_event_page(api_key, *, since, until, after, opener=None):
    if not isinstance(api_key, str) or not re.fullmatch(r"sk_test_[A-Za-z0-9_]{11,504}", api_key):
        raise BillingUnavailable("Test-mode event reader unavailable")
    query = {
        "created[gte]": since,
        "created[lte]": until,
        "types[]": sorted(EVENT_LABELS),
        "limit": PAGE_SIZE,
    }
    if after:
        if not provider_id(after, "evt"):
            raise EventCursorError()
        query["starting_after"] = after
    url = "https://api.stripe.com/v1/events?" + urllib.parse.urlencode(query, doseq=True)
    auth = base64.b64encode((api_key + ":").encode()).decode()
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Basic " + auth,
            "Stripe-Version": "2025-02-24.acacia",
            "Accept": "application/json",
        },
    )
    send = opener or urllib.request.build_opener(_NoRedirect()).open
    with send(request, timeout=3) as response:
        raw = response.read(1048577)
        if response.status != 200 or len(raw) > 1048576:
            raise BillingUnavailable("Stripe event page unavailable")
    return _strict_provider_json(raw)


def _utc(seconds):
    return (
        datetime.fromtimestamp(seconds, timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _matches(event, kind, record):
    # Account/context events can describe another platform account.
    if event.get("account") is not None or event.get("context") is not None:
        return False
    data = event.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    if not isinstance(obj, dict) or obj.get("livemode") is not False:
        raise BillingUnavailable("Invalid Stripe event snapshot")
    if obj.get("customer") != record.payer_id:
        return False
    if kind.startswith("customer.subscription."):
        return obj.get("object") == "subscription" and obj.get("id") == record.stripe_subscription_id
    if obj.get("object") != "invoice" or not provider_id(obj.get("id"), "in"):
        raise BillingUnavailable("Invalid invoice snapshot")
    # Stripe preserves each event's original API version. Support invoice
    # snapshots on both sides of the introduction of invoice.parent.
    refs = []
    if "subscription" in obj:
        refs.append(obj["subscription"])
    parent = obj.get("parent")
    if parent is not None:
        if not isinstance(parent, dict):
            raise BillingUnavailable("Invalid invoice parent")
        details = parent.get("subscription_details")
        if parent.get("type") == "subscription_details" and isinstance(details, dict):
            refs.append(details.get("subscription"))
        else:
            refs.append(None)
    return bool(refs) and all(value == record.stripe_subscription_id for value in refs)


class AdminBillingEvents:
    def __init__(self, history, fetch, cursor_key):
        if history.reporter.expected_livemode is not False or len(cursor_key) < 32:
            raise ValueError("A bound test-mode reader and independent cursor key are required")
        self.history, self.fetch = history, fetch
        self.cipher = Fernet(
            base64.urlsafe_b64encode(hmac.digest(cursor_key, b"guardoath-stripe-events-v1", "sha256"))
        )

    @staticmethod
    def binding(record):
        return [
            record.tenant_id,
            record.demo,
            record.billing_mode,
            record.payer_id,
            record.stripe_subscription_id,
            record.revision,
        ]

    def _resume(self, cursor, scope, now):
        try:
            encoded = cursor.encode("ascii")
            if base64.urlsafe_b64encode(base64.urlsafe_b64decode(encoded)) != encoded:
                raise ValueError("Invalid cursor encoding")
            raw = self.cipher.decrypt_at_time(encoded, ttl=CURSOR_TTL, current_time=now)
            bound, end, timestamp, after = json.loads(raw)
            if (
                bound != scope
                or type(end) is not int
                or not 0 <= now - end <= CURSOR_TTL
                or type(timestamp) is not int
                or not end - WINDOW <= timestamp <= end
                or not provider_id(after, "evt")
            ):
                raise ValueError()
            return end, timestamp, after
        except (ValueError, InvalidToken, UnicodeError, TypeError) as exc:
            raise EventCursorError() from exc

    def read(self, record, actor, cursor):
        now = self.history._now()
        if (
            record.demo
            or record.billing_mode != "stripe"
            or not record.payer_id
            or not record.stripe_subscription_id
        ):
            if cursor:
                raise EventCursorError()
            return {"verified": False, "events": [], "next_cursor": None}
        scope = [actor, self.binding(record), self.history.reporter.expected_price_id]
        end, previous, after = self._resume(cursor, scope, now) if cursor else (now, now, None)
        self.history._reserve_read(actor, now)
        if self.history.reporter.read(record).get("verified") is not True:
            raise BillingUnavailable("Subscription binding unavailable")
        rows = []
        seen = {after}
        for _ in range(3):
            page = self.fetch(since=end - WINDOW, until=end, after=after)
            entries = page.get("data")
            more = page.get("has_more")
            if (
                page.get("object") != "list"
                or not isinstance(entries, list)
                or len(entries) > PAGE_SIZE
                or type(more) is not bool
                or (more and not entries)
            ):
                raise BillingUnavailable("Invalid Stripe event page")
            for event in entries:
                if not isinstance(event, dict):
                    raise BillingUnavailable("Invalid Stripe event")
                event_id, created, kind = event.get("id"), event.get("created"), event.get("type")
                if (
                    event.get("object") != "event"
                    or not provider_id(event_id, "evt")
                    or event_id in seen
                    or event.get("livemode") is not False
                    or not isinstance(kind, str)
                    or kind not in EVENT_LABELS
                    or type(created) is not int
                    or not end - WINDOW <= created <= previous
                ):
                    raise BillingUnavailable("Invalid Stripe event envelope")
                seen.add(event_id)
                previous, after = created, event_id
                if _matches(event, kind, record):
                    rows.append(
                        {"occurred_utc": _utc(created), "type": kind, "label": EVENT_LABELS[kind]}
                    )
            if rows or not more:
                break
        next_cursor = None
        if more:
            raw = json.dumps([scope, end, previous, after], separators=(",", ":")).encode()
            next_cursor = self.cipher.encrypt_at_time(raw, current_time=now).decode()
        return {
            "verified": True,
            "from_utc": _utc(end - WINDOW),
            "through_utc": _utc(end),
            "events": rows,
            "next_cursor": next_cursor,
        }
