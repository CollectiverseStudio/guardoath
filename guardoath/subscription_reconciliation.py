"""Fail-closed Stripe subscription entitlement decision from a provider-fetched snapshot.

This module does not trust webhook payloads, browser claims, or checkout redirects.
A caller must retrieve the subscription directly from Stripe using a server-side
credential, then pass its response with the tenant's server-owned billing binding.
"""
from dataclasses import dataclass
from typing import Any


class SubscriptionReconciliationError(ValueError):
    """Provider state cannot safely be mapped to this tenant."""


@dataclass(frozen=True)
class StripeBinding:
    tenant_id: str
    customer_id: str
    subscription_id: str

    def __post_init__(self):
        # Even server-owned billing bindings may have come from legacy state.
        # Reject malformed identifiers here, before they reach URL builders,
        # tenant record writes, or subscription entitlement decisions.
        if (not isinstance(self.tenant_id, str)
                or not 1 <= len(self.tenant_id) <= 40
                or any(not (c.isascii() and (c.isalnum() or c in "-_"))
                       for c in self.tenant_id)):
            raise SubscriptionReconciliationError("Invalid server-owned billing binding")
        for value, prefix in ((self.customer_id, "cus_"), (self.subscription_id, "sub_")):
            if (not isinstance(value, str) or not value.startswith(prefix)
                    or not 1 <= len(value[len(prefix):]) <= 100 - len(prefix)
                    or not all(c.isascii() and (c.isalnum() or c == "_")
                               for c in value[len(prefix):])):
                raise SubscriptionReconciliationError("Invalid server-owned billing binding")


@dataclass(frozen=True)
class EntitlementDecision:
    tenant_id: str
    status: str
    active: bool
    subscription_id: str


def reconcile_subscription(binding: StripeBinding, provider_subscription: dict[str, Any]) -> EntitlementDecision:
    """Map a trusted provider fetch to a tenant status; never use an event snapshot.

    Only an exact customer and subscription binding can activate the account.
    Past-due, trialing, incomplete and unknown states fail closed by default.
    """
    if not isinstance(provider_subscription, dict):
        raise SubscriptionReconciliationError("Invalid provider response")
    if provider_subscription.get("id") != binding.subscription_id:
        raise SubscriptionReconciliationError("Subscription belongs to a different billing binding")
    customer = provider_subscription.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    if customer != binding.customer_id:
        raise SubscriptionReconciliationError("Customer belongs to a different billing binding")
    if provider_subscription.get("object") != "subscription":
        raise SubscriptionReconciliationError("Not a Stripe subscription")
    status = provider_subscription.get("status")
    if not isinstance(status, str):
        raise SubscriptionReconciliationError("Missing provider subscription status")
    mapped = {
        "active": "active",
        "past_due": "past_due",
        "canceled": "canceled",
        "unpaid": "inactive",
        "incomplete": "inactive",
        "incomplete_expired": "inactive",
        "trialing": "inactive",
        "paused": "inactive",
    }.get(status, "inactive")
    return EntitlementDecision(binding.tenant_id, mapped, mapped == "active", binding.subscription_id)


def reconciled_allowance(record, *, active: bool, plan_monthly_allowance: int | None) -> dict:
    """Apply a trusted plan without erasing a saved operator request cap.

    Legacy bound subscriptions lack cap provenance. Preserve a lower saved
    allowance (including zero after delinquency) until it can be reviewed;
    an active provider is never authority to silently remove a budget control.
    First activation can use the configured plan. Once recorded, plan and cap
    remain separate across subsequent delinquency/reactivation transitions.
    """
    if type(active) is not bool or (plan_monthly_allowance is not None and
            (type(plan_monthly_allowance) is not int or
             not 1 <= plan_monthly_allowance <= 1_000_000_000)):
        raise ValueError("Invalid server-configured plan allowance")
    current = record.monthly_allowance
    cap = record.admin_monthly_allowance_cap
    prior_plan = record.stripe_plan_monthly_allowance
    if plan_monthly_allowance is not None and record.stripe_subscription_id is not None:
        if prior_plan is None and cap is None:
            cap = current
        elif (record.subscription_status == "active" and prior_plan is not None
              and current < prior_plan):
            # Also retain restrictions written by older operators after a
            # newer reconciliation first recorded the nominal plan.
            cap = current if cap is None else min(cap, current)
    nominal = plan_monthly_allowance if plan_monthly_allowance is not None else current
    allowance = min(nominal, cap) if cap is not None else nominal
    return {
        "monthly_allowance": allowance if active else 0,
        "overage": "block" if not active or plan_monthly_allowance is not None or cap is not None else record.overage,
        "admin_monthly_allowance_cap": cap,
        "stripe_plan_monthly_allowance": plan_monthly_allowance if plan_monthly_allowance is not None else prior_plan,
    }


def require_supported_entitlement(snapshot: dict, *, active: bool) -> None:
    """Admission policy for the configured single-item automatic-payment plan.

    Call after verifying customer, subscription, price and environment against
    server configuration. Read projections may still display paused or manually
    invoiced subscriptions; neither is authority to activate this plan. Stripe
    can report manual-invoice subscriptions as active before receiving payment.
    """
    items = snapshot.get("items") if isinstance(snapshot, dict) else None
    entries = items.get("data") if isinstance(items, dict) else None
    if (type(active) is not bool or not isinstance(entries, list)
            or items.get("has_more") is not False or len(entries) != 1
            or not isinstance(entries[0], dict)
            or type(entries[0].get("quantity")) is not int
            or not 1 <= entries[0]["quantity"] <= 1_000_000
            or (active and entries[0]["quantity"] != 1)):
        raise SubscriptionReconciliationError("Unsupported subscription quantity")
    if active and ("pause_collection" not in snapshot or snapshot["pause_collection"] is not None
                   or snapshot.get("collection_method") != "charge_automatically"):
        raise SubscriptionReconciliationError("Automatic payment collection must be verified")
