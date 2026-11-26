"""Pure admission proof for restoring a reviewed local request cap.

The caller independently retrieves the bound subscription through the existing
fixed-host Stripe reader. This module validates that response against trusted
configuration and the caller's current clock; it performs no provider requests.
The proof describes subscription entitlement, not an account-identity check or
proof of collected revenue. Tenant status and the configured allowance remain
the restoration service's responsibility.
"""
from __future__ import annotations

from guardoath.admin_cancellation_provider import provider_id
from guardoath.subscription_reconciliation import StripeBinding


MAX_TIME = 253402300799


class CapRestorationEntitlementError(Exception):
    """The current supported Stripe entitlement could not be established."""


def _timestamp(value) -> bool:
    return type(value) is int and 0 < value <= MAX_TIME


def validate_restoration_entitlement(snapshot, binding, *, expected_price_id,
                                     expected_livemode, now) -> dict:
    """Return the ten-field, current-period proof from a direct provider read.

    Missing fields are unknown. Only the configured single-item automatic-payment
    plan is admitted. An ordinary future period-end cancellation remains intact;
    custom schedules, pending updates and ambiguous cancellation dates are refused.
    The caller must refresh this proof before the conditional local state write.
    """
    try:
        if (not isinstance(binding, StripeBinding)
                or not provider_id(expected_price_id, "price")
                or type(expected_livemode) is not bool or not _timestamp(now)):
            raise ValueError
        # Frozen dataclasses can still be forged through low-level construction.
        # Revalidate the independently loaded binding before matching a response.
        StripeBinding(binding.tenant_id, binding.customer_id, binding.subscription_id)
        if not isinstance(snapshot, dict):
            raise ValueError
        customer = snapshot["customer"]
        if isinstance(customer, dict):
            if (customer.get("object") != "customer"
                    or ("deleted" in customer and customer["deleted"] is not False)
                    or ("livemode" in customer and customer["livemode"] is not expected_livemode)):
                raise ValueError
            customer = customer["id"]
        start, end = snapshot["current_period_start"], snapshot["current_period_end"]
        scheduled, cancel_at = snapshot["cancel_at_period_end"], snapshot["cancel_at"]
        if (snapshot["object"] != "subscription"
                or snapshot["id"] != binding.subscription_id
                or customer != binding.customer_id
                or snapshot["livemode"] is not expected_livemode
                or snapshot["status"] != "active"
                or snapshot["collection_method"] != "charge_automatically"
                or snapshot["pause_collection"] is not None
                or snapshot["schedule"] is not None
                or snapshot["pending_update"] is not None
                or not _timestamp(start) or not _timestamp(end)
                or not start <= now < end
                or type(scheduled) is not bool
                or (cancel_at is not None and
                    (not scheduled or not _timestamp(cancel_at) or cancel_at != end))):
            raise ValueError
        items = snapshot["items"]
        entries = items["data"]
        if (not isinstance(items, dict) or items["object"] != "list"
                or items["has_more"] is not False
                or not isinstance(entries, list) or len(entries) != 1
                or ("total_count" in items and
                    (type(items["total_count"]) is not int or items["total_count"] != 1))):
            raise ValueError
        item = entries[0]
        if (not isinstance(item, dict) or item["object"] != "subscription_item"
                or not provider_id(item["id"], "si")
                or item["subscription"] != binding.subscription_id
                or type(item["quantity"]) is not int or item["quantity"] != 1
                or ("deleted" in item and item["deleted"] is not False)
                or ("current_period_start" in item and
                    (not _timestamp(item["current_period_start"]) or item["current_period_start"] != start))
                or ("current_period_end" in item and
                    (not _timestamp(item["current_period_end"]) or item["current_period_end"] != end))):
            raise ValueError
        price = item["price"]
        if isinstance(price, dict):
            if (price.get("object") != "price"
                    or price.get("livemode") is not expected_livemode
                    or ("deleted" in price and price["deleted"] is not False)):
                raise ValueError
            price = price["id"]
        if price != expected_price_id:
            raise ValueError
        return {"livemode": expected_livemode,
                "customer_id": binding.customer_id,
                "subscription_id": binding.subscription_id,
                "price_id": expected_price_id, "item_id": item["id"], "quantity": 1,
                "period_start": start, "period_end": end,
                "cancel_at_period_end": scheduled, "cancel_at": cancel_at}
    except Exception:
        # Do not expose provider content, billing identifiers or malformed values.
        raise CapRestorationEntitlementError(
            "Current supported Stripe entitlement could not be established") from None
