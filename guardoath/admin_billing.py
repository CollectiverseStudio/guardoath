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
        if getattr(record, "demo", True):
            return {"status": "not_applicable", "provider": "Stripe", "verified": False}
        tenant_id = getattr(record, "tenant_id", "")
        customer_id = getattr(record, "payer_id", None)
        subscription_id = getattr(record, "stripe_subscription_id", None)
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
        if state not in STATES:
            raise BillingUnavailable("Unrecognized Stripe subscription status")
        stored_status = getattr(record, "subscription_status", None)
        tenant_status = getattr(record, "status", None)
        billing_mode = getattr(record, "billing_mode", None)
        # An admin display never grants entitlement. Only show a concordant
        # active entitlement after independent provider AND saved-state checks.
        concordant = (state == "active" and stored_status == "active"
                      and tenant_status == "active" and billing_mode == "stripe")
        # Billing-cycle metadata is advisory and must come from the same verified
        # provider snapshot. Missing/malformed fields are unknown, never a
        # projected invoice amount or authorization to grant paid access.
        cancel_flag = snapshot.get("cancel_at_period_end")
        cancel_flag = cancel_flag if type(cancel_flag) is bool else None
        period_end = snapshot.get("current_period_end")
        period_end_utc = (datetime.fromtimestamp(period_end, tz=timezone.utc)
                          .isoformat(timespec="seconds").replace("+00:00", "Z")
                          if type(period_end) is int and 946684800 <= period_end <= 4102444800
                          else None)
        return {
            "status": state, "provider": "Stripe", "verified": True,
            "cancel_at_period_end": cancel_flag,
            "current_period_end_utc": period_end_utc,
            "billing_cycle_source": "same verified Stripe subscription (not an invoice)",
            "price_verified": True, "provider_active": state == "active",
            "subscription_entitlement": concordant,
            "stored_status": stored_status if stored_status in
                {"active", "inactive", "past_due", "canceled"} else "unknown",
            "entitlement_source": "direct Stripe subscription plus persisted tenant state",
            "reconciliation": _reconciliation_assessment(
                state, stored_status, tenant_status, billing_mode),
            "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": "Read-only verification; no Stripe or tenant entitlement was modified",
        }
