"""Minimal, read-only, provider-verified subscription reporting for owners.

Never accepts a Stripe customer/subscription ID from the browser. A tenant's
server-owned binding is independently checked against the provider response,
and no subscription entitlement is changed by an administrative read.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Callable

CUSTOMER = re.compile(r"^cus_[A-Za-z0-9_]{1,96}$")
SUBSCRIPTION = re.compile(r"^sub_[A-Za-z0-9_]{1,96}$")


class BillingUnavailable(Exception):
    pass


class AdminSubscriptionReporter:
    def __init__(self, fetch: Callable[[str,str,str], dict]):
        if not callable(fetch):
            raise ValueError("A trusted server-side Stripe fetch callback is required")
        self.fetch = fetch

    def read(self, record) -> dict:
        if getattr(record, "demo", True):
            return {"status":"not_applicable", "provider":"Stripe", "verified":False}
        tenant_id = getattr(record, "tenant_id", "")
        customer_id = getattr(record, "payer_id", None)
        subscription_id = getattr(record, "stripe_subscription_id", None)
        if not customer_id or not subscription_id:
            return {"status":"unbound", "provider":"Stripe", "verified":False}
        if not CUSTOMER.fullmatch(customer_id) or not SUBSCRIPTION.fullmatch(subscription_id):
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
        state = snapshot.get("status")
        if state not in {"active", "past_due", "canceled", "unpaid", "incomplete", "incomplete_expired", "trialing", "paused"}:
            raise BillingUnavailable("Unrecognized Stripe subscription status")
        # Return status with an explicit source. Never expose raw provider
        # objects, customer IDs, payment methods, contact details or secrets.
        return {"status":state,"provider":"Stripe", "verified":True,
                "subscription_entitlement":state=="active",
                "verified_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "note":"Provider snapshot; no customer entitlement or billing record was modified"}
