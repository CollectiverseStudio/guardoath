"""Stripe entitlement mapping cannot be influenced by customer or webhook claims."""
import pytest

from guardoath.subscription_reconciliation import (
    StripeBinding, SubscriptionReconciliationError, reconcile_subscription,
)

BINDING = StripeBinding("usr-test-account", "cus_owned", "sub_owned")


def snapshot(status="active", **changes):
    return {"object": "subscription", "id": "sub_owned",
            "customer": "cus_owned", "status": status, **changes}


def test_only_exact_provider_active_subscription_grants_access():
    decision = reconcile_subscription(BINDING, snapshot())
    assert decision.tenant_id == "usr-test-account"
    assert decision.status == "active" and decision.active is True
    assert decision.subscription_id == "sub_owned"


@pytest.mark.parametrize("status,expected", [
    ("past_due", "past_due"), ("canceled", "canceled"),
    ("unpaid", "inactive"), ("incomplete", "inactive"),
    ("incomplete_expired", "inactive"), ("trialing", "inactive"),
    ("paused", "inactive"), ("unknown_future_state", "inactive"),
])
def test_all_non_active_states_fail_closed(status, expected):
    decision = reconcile_subscription(BINDING, snapshot(status))
    assert decision.status == expected and not decision.active


@pytest.mark.parametrize("changes", [
    {"id": "sub_other"}, {"customer": "cus_other"},
    {"object": "checkout.session"}, {"status": None},
    {"customer": {"id": "cus_other"}},
])
def test_foreign_or_invalid_provider_snapshot_is_rejected(changes):
    with pytest.raises(SubscriptionReconciliationError):
        reconcile_subscription(BINDING, snapshot(**changes))


def test_expanded_customer_object_is_supported_only_when_matching():
    assert reconcile_subscription(BINDING, snapshot(customer={"id": "cus_owned"})).active


def test_invalid_server_binding_is_rejected():
    with pytest.raises(SubscriptionReconciliationError):
        StripeBinding("usr-test-account", "cus_owned", "sub_wrong"[:3])
