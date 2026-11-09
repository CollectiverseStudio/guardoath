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
        if not self.tenant_id or not self.customer_id.startswith("cus_") or not self.subscription_id.startswith("sub_"):
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
