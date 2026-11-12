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
