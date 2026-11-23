"""Read-only Stripe subscription verification for the owner console.

A provider 'active' status by itself is NOT authorization to use GuardOath.
Verify the server-owned binding, configured plan price, Stripe environment and
current provider state, then compare with persisted tenant entitlement. This
module never writes or reconciles entitlements, invoices or tenant records.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Callable

CUSTOMER = re.compile(r"^cus_[A-Za-z0-9_]{1,96}$")
SUBSCRIPTION = re.compile(r"^sub_[A-Za-z0-9_]{1,96}$")
PRICE = re.compile(r"^price_[A-Za-z0-9_]{1,96}$")
STATES = frozenset({"active", "past_due", "canceled", "unpaid", "incomplete",
                    "incomplete_expired", "trialing", "paused"})
COLLECTION_BEHAVIORS = frozenset({"keep_as_draft", "mark_uncollectible", "void"})
COLLECTION_METHODS = frozenset({"charge_automatically", "send_invoice"})
_MISSING = object()


class BillingUnavailable(Exception):
    pass



def _reconciliation_assessment(provider_status, stored_status, tenant_status, billing_mode):
    """Triage two independent sources; never infer or mutate an entitlement."""
    if (tenant_status == "active" and billing_mode == "stripe" and
            stored_status == "active" and provider_status != "active"):
        return {"level": "critical",
                "code": "saved_active_without_verified_provider",
                "next_step": "Investigate trusted reconciliation; use audited tenant suspension if access must be blocked"}
    if provider_status == "active" and tenant_status != "active":
        return {"level": "review", "code": "provider_active_tenant_disabled",
                "next_step": "Review billing while tenant access is suspended"}
    if provider_status == "active" and billing_mode != "stripe":
        return {"level": "review", "code": "provider_active_billing_mode_mismatch",
                "next_step": "Review trusted billing binding; never override tenant entitlement here"}
    if provider_status == "active" and stored_status != "active":
        return {"level": "review", "code": "provider_active_saved_inactive",
                "next_step": "Inspect webhook/reconciliation health; no manual subscription unlock"}
    if provider_status is None:
        return {"level": "unavailable", "code": "no_provider_binding",
                "next_step": "Provider verification requires a saved customer and subscription binding"}
    if provider_status != "active":
        # A disabled tenant can still carry a stale *saved* active subscription.
        # Do not report agreement just because it is currently suspended.
        if stored_status == "active":
            return {"level": "critical", "code": "saved_active_without_verified_provider",
                    "next_step": "Investigate trusted reconciliation; use audited tenant suspension if access must be blocked"}
        # Provider states can intentionally collapse into the tenant's
        # conservative inactive state. Everything else is drift, not agreement.
        expected_saved = {"past_due": "past_due", "canceled": "canceled",
                          "unpaid": "inactive", "incomplete": "inactive",
                          "incomplete_expired": "inactive", "trialing": "inactive",
                          "paused": "inactive"}.get(provider_status)
        if expected_saved is None:
            return {"level": "unavailable", "code": "unrecognized_provider_state",
                    "next_step": "Verify the subscription provider state independently"}
        if stored_status != expected_saved:
            return {"level": "review", "code": "saved_nonactive_state_differs_from_provider",
                    "next_step": "Review trusted Stripe reconciliation; do not grant access or rewrite saved billing state"}
    return {"level": "consistent", "code": "states_agree",
            "next_step": "No billing mutation performed; inspect Stripe provider if needed"}


class AdminSubscriptionReporter:
    def __init__(self, fetch: Callable[[str, str, str], dict], *,
                 expected_price_id: str, expected_livemode: bool):
        if (not callable(fetch) or not isinstance(expected_price_id, str)
                or not PRICE.fullmatch(expected_price_id)
                or type(expected_livemode) is not bool):
            raise ValueError("A trusted Stripe fetch, price and environment are required")
        self.fetch = fetch
        self.expected_price_id = expected_price_id
        self.expected_livemode = expected_livemode

    def read(self, record) -> dict:
        # A damaged saved identity or billing state cannot masquerade as
        # a demo or an eligible paid account. Verify it before any Stripe read.
        demo = getattr(record, "demo", None)
        if type(demo) is not bool:
            raise BillingUnavailable("Saved customer identity unavailable")
        if demo:
            return {"status": "not_applicable", "provider": "Stripe", "verified": False}
        tenant_id = getattr(record, "tenant_id", "")
        customer_id = getattr(record, "payer_id", None)
        subscription_id = getattr(record, "stripe_subscription_id", None)
        stored_status = getattr(record, "subscription_status", None)
        tenant_status = getattr(record, "status", None)
        billing_mode = getattr(record, "billing_mode", None)
        if (not isinstance(tenant_id, str) or not tenant_id.startswith("usr-")
                or not isinstance(tenant_status, str) or tenant_status not in ("active", "disabled")
                or not isinstance(billing_mode, str) or billing_mode not in ("stripe", "partner", "internal")
                or (stored_status is not None and
                    (not isinstance(stored_status, str) or stored_status not in
                     ("active", "inactive", "past_due", "canceled")))
                or (customer_id is not None and
                    (not isinstance(customer_id, str) or not CUSTOMER.fullmatch(customer_id)))
                or (subscription_id is not None and
                    (not isinstance(subscription_id, str) or not SUBSCRIPTION.fullmatch(subscription_id)))):
            raise BillingUnavailable("Saved Stripe binding or entitlement unavailable")
        if not customer_id or not subscription_id:
            return {"status": "unbound", "provider": "Stripe", "verified": False,
                    "reconciliation": _reconciliation_assessment(
                        None, getattr(record, "subscription_status", None),
                        getattr(record, "status", None), getattr(record, "billing_mode", None))}
        if (not isinstance(tenant_id, str) or not tenant_id.startswith("usr-")
                or not isinstance(customer_id, str) or not CUSTOMER.fullmatch(customer_id)
                or not isinstance(subscription_id, str) or not SUBSCRIPTION.fullmatch(subscription_id)):
            raise BillingUnavailable("Invalid internal Stripe binding")
        try:
            snapshot = self.fetch(tenant_id, customer_id, subscription_id)
        except Exception as exc:
            raise BillingUnavailable("Unable to retrieve trusted Stripe subscription") from exc
        if not isinstance(snapshot, dict) or snapshot.get("id") != subscription_id:
            raise BillingUnavailable("Stripe subscription binding mismatch")
        customer = snapshot.get("customer")
        if isinstance(customer, dict):
            customer = customer.get("id")
        if customer != customer_id or snapshot.get("object") != "subscription":
            raise BillingUnavailable("Stripe customer binding mismatch")
        if snapshot.get("livemode") is not self.expected_livemode:
            raise BillingUnavailable("Stripe environment mismatch")
        items = snapshot.get("items")
        entries = items.get("data") if isinstance(items, dict) else None
        if (not isinstance(entries, list) or len(entries) != 1 or
                items.get("has_more") is not False or not isinstance(entries[0], dict)):
            raise BillingUnavailable("Incomplete or ambiguous Stripe plan")
        price = entries[0].get("price")
        actual_price = price.get("id") if isinstance(price, dict) else price
        if actual_price != self.expected_price_id:
            raise BillingUnavailable("Stripe subscription plan mismatch")
        # Stripe items normally include a positive quantity. An empty/zero or
        # malformed item must never become a paid entitlement merely because
        # the subscription object says 'active'. Quantity is checked server-side.
        quantity = entries[0].get("quantity")
        if type(quantity) is not int or not 1 <= quantity <= 1000000:
            raise BillingUnavailable("Invalid Stripe subscription quantity")
        state = snapshot.get("status")
        # A provider JSON list, object or bool is not a Stripe state. Reject
        # unhashable values as unavailable, never an exception or entitlement.
        if not isinstance(state, str) or state not in STATES:
            raise BillingUnavailable("Unrecognized Stripe subscription status")

        # Stripe pause_collection is independent of subscription.status. A
        # subscription can remain "active" while collecting no payments.
        # Missing fields stay unknown rather than being called "not paused".
        pause = snapshot.get("pause_collection", _MISSING)
        collection_status, collection_behavior, collection_resumes_at_utc = (
            "unavailable", None, None)
        if pause is None:
            collection_status = "collecting"
        elif pause is not _MISSING:
            if not isinstance(pause, dict):
                raise BillingUnavailable("Invalid Stripe payment collection state")
            behavior = pause.get("behavior")
            resumes_at = pause.get("resumes_at")
            if (not isinstance(behavior, str) or behavior not in COLLECTION_BEHAVIORS
                    or (resumes_at is not None and
                        (type(resumes_at) is not int or
                         not 946684800 <= resumes_at <= 4102444800))):
                raise BillingUnavailable("Invalid Stripe payment collection state")
            collection_status = "paused"
            collection_behavior = behavior
            if resumes_at is not None:
                collection_resumes_at_utc = (
                    datetime.fromtimestamp(resumes_at, timezone.utc)
                    .isoformat(timespec="seconds").replace("+00:00", "Z"))
        # Stripe can mark a manually invoiced subscription active before an
        # invoice is paid. Neither collection method nor pause proves revenue.
        collection_method = snapshot.get("collection_method", _MISSING)
        if collection_method is _MISSING:
            method_status = "unavailable"
        elif isinstance(collection_method, str) and collection_method in COLLECTION_METHODS:
            method_status = collection_method
        else:
            raise BillingUnavailable("Invalid Stripe invoice collection method")
        assessment = _reconciliation_assessment(
            state, stored_status, tenant_status, billing_mode)
        if collection_status == "paused" and assessment["level"] == "consistent":
            assessment = {
                "level": "review",
                "code": "stripe_payment_collection_paused",
                "next_step": "Review Stripe payment collection pause; active subscription status does not prove payments are collecting",
            }
        elif method_status == "send_invoice" and assessment["level"] == "consistent":
            assessment = {
                "level": "review",
                "code": "stripe_manual_invoice_collection",
                "next_step": "Stripe sends invoices rather than charging automatically; inspect verified invoices before treating an active subscription as collected revenue",
            }
        # An admin display never grants entitlement. Only show a concordant
        # active entitlement after independent provider AND saved-state checks.
        concordant = (state == "active" and stored_status == "active"
                      and tenant_status == "active" and billing_mode == "stripe")
        # Billing-cycle metadata is advisory and must come from the same verified
        # provider snapshot. Missing/malformed fields are unknown, never a
        # projected invoice amount or authorization to grant paid access.
        cancel_flag = snapshot.get("cancel_at_period_end")
        cancel_flag = cancel_flag if type(cancel_flag) is bool else None
        # The pinned Stripe API still supports subscription-level periods.
        # Newer versions use item periods; accept only our single verified item.
        # Malformed or disagreeing periods remain explicitly unavailable.
        legacy_period = snapshot.get("current_period_end", _MISSING)
        item_period = entries[0].get("current_period_end", _MISSING)
        def valid_period(value):
            return type(value) is int and 946684800 <= value <= 4102444800
        if item_period is not _MISSING:
            period_end = (item_period if valid_period(item_period) and
                          (legacy_period is _MISSING or legacy_period == item_period)
                          else None)
            period_source = "subscription_item" if period_end is not None else "unavailable"
        elif valid_period(legacy_period):
            period_end, period_source = legacy_period, "subscription"
        else:
            period_end, period_source = None, "unavailable"
        period_end_utc = (datetime.fromtimestamp(period_end, tz=timezone.utc)
                          .isoformat(timespec="seconds").replace("+00:00", "Z")
                          if period_end is not None else None)
        return {
            "status": state, "provider": "Stripe", "verified": True,
            "cancel_at_period_end": cancel_flag,
            "current_period_end_utc": period_end_utc,
            "current_period_source": period_source,
            "billing_cycle_source": "same verified Stripe subscription (not an invoice)",
            "collection_status": collection_status,
            "collection_method": method_status,
            "collection_behavior": collection_behavior,
            "collection_resumes_at_utc": collection_resumes_at_utc,
            "price_verified": True, "provider_active": state == "active",
            "subscription_entitlement": concordant,
            "stored_status": stored_status if stored_status in
                {"active", "inactive", "past_due", "canceled"} else "unknown",
            "entitlement_source": "direct Stripe subscription plus persisted tenant state",
            "reconciliation": assessment,
            "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": "Read-only verification; payment collection is separate from subscription status; no Stripe or tenant entitlement was modified",
        }
