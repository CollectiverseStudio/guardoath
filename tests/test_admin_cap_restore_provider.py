"""Direct subscription proof required before a local request-cap increase."""
from copy import deepcopy

import pytest

from guardoath.admin_cap_restore_provider import (
    CapRestorationEntitlementError, validate_restoration_entitlement,
)
from guardoath.subscription_reconciliation import StripeBinding


NOW = 1795700000
BINDING = StripeBinding("usr-cap-restore", "cus_restore", "sub_restore")
CONFIG = {"expected_price_id": "price_plan", "expected_livemode": False, "now": NOW}


def snapshot():
    return {
        "object": "subscription", "id": "sub_restore", "customer": "cus_restore",
        "livemode": False, "status": "active", "collection_method": "charge_automatically",
        "pause_collection": None, "schedule": None, "pending_update": None,
        "current_period_start": NOW - 3600, "current_period_end": NOW + 86400,
        "cancel_at_period_end": False, "cancel_at": None,
        "items": {"object": "list", "has_more": False, "data": [{
            "object": "subscription_item", "id": "si_restore", "subscription": "sub_restore",
            "quantity": 1, "price": {"object": "price", "id": "price_plan", "livemode": False},
        }]},
    }


def validate(value=None, binding=BINDING, **config):
    return validate_restoration_entitlement(
        snapshot() if value is None else value, binding, **{**CONFIG, **config})


def test_normalized_proof_is_exact_and_does_not_change_the_provider_snapshot():
    value = snapshot()
    value["metadata"] = {"private": "PRIVATE metadata"}
    original = deepcopy(value)
    proof = validate(value)
    assert proof == {
        "livemode": False, "customer_id": "cus_restore", "subscription_id": "sub_restore",
        "price_id": "price_plan", "item_id": "si_restore", "quantity": 1,
        "period_start": NOW - 3600, "period_end": NOW + 86400,
        "cancel_at_period_end": False, "cancel_at": None,
    }
    assert value == original
    proof["period_end"] = NOW
    assert value["current_period_end"] == NOW + 86400


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("expanded_customer", [False, True])
@pytest.mark.parametrize("expanded_price", [False, True])
def test_bound_live_and_test_objects_or_identifiers(live, expanded_customer, expanded_price):
    value = snapshot()
    value["livemode"] = live
    if expanded_customer:
        value["customer"] = {"object": "customer", "id": "cus_restore", "livemode": live}
    value["items"]["data"][0]["price"] = (
        {"object": "price", "id": "price_plan", "livemode": live} if expanded_price else "price_plan")
    assert validate(value, expected_livemode=live)["livemode"] is live


@pytest.mark.parametrize("cancel_at", [None, NOW + 86400])
def test_future_period_end_cancellation_remains_scheduled(cancel_at):
    value = snapshot()
    value.update(cancel_at_period_end=True, cancel_at=cancel_at, canceled_at=NOW - 100)
    item = value["items"]["data"][0]
    item.update(current_period_start=NOW - 3600, current_period_end=NOW + 86400)
    value["items"]["total_count"] = 1
    proof = validate(value)
    assert proof["cancel_at_period_end"] is True and proof["cancel_at"] == cancel_at
    assert proof["period_end"] == NOW + 86400


def test_period_start_is_inclusive_and_period_end_is_exclusive():
    assert validate(now=NOW - 3600)["period_start"] == NOW - 3600
    assert validate(now=NOW + 86399)["period_end"] == NOW + 86400
    for now in (NOW - 3601, NOW + 86400):
        with pytest.raises(CapRestorationEntitlementError):
            validate(now=now)


@pytest.mark.parametrize("field", [
    "object", "id", "customer", "livemode", "status", "collection_method", "pause_collection",
    "schedule", "pending_update", "current_period_start", "current_period_end",
    "cancel_at_period_end", "cancel_at", "items",
])
def test_missing_subscription_proof_is_unknown(field):
    value = snapshot()
    del value[field]
    with pytest.raises(CapRestorationEntitlementError):
        validate(value)


@pytest.mark.parametrize("field,wrong", [
    ("object", "invoice"), ("id", "sub_other"), ("customer", "cus_other"),
    ("customer", {"id": "cus_restore"}), ("customer", {"object": "subscription", "id": "cus_restore"}),
    ("customer", {"object": "customer", "id": "cus_restore", "deleted": True}),
    ("customer", {"object": "customer", "id": "cus_restore", "livemode": True}),
    ("livemode", True), ("livemode", 0), ("livemode", "false"),
    ("status", "past_due"), ("status", "trialing"), ("status", "canceled"),
    ("status", "paused"), ("status", "unpaid"), ("status", "incomplete"),
    ("status", "incomplete_expired"), ("status", "unknown"), ("status", []),
    ("collection_method", "send_invoice"), ("collection_method", None),
    ("pause_collection", {"behavior": "keep_as_draft"}), ("pause_collection", False),
    ("schedule", "sub_sched_future"), ("schedule", {}),
    ("pending_update", {}), ("pending_update", False),
    ("cancel_at_period_end", 0), ("cancel_at_period_end", 1), ("cancel_at_period_end", None),
    ("cancel_at_period_end", "false"), ("cancel_at", NOW + 86400),
])
def test_wrong_binding_or_unsupported_subscription_state_refuses(field, wrong):
    value = snapshot()
    value[field] = wrong
    with pytest.raises(CapRestorationEntitlementError):
        validate(value)


@pytest.mark.parametrize("field,wrong", [
    ("current_period_start", True), ("current_period_start", 0),
    ("current_period_start", NOW + 1), ("current_period_start", NOW - 3600.0),
    ("current_period_start", str(NOW - 3600)), ("current_period_start", float("nan")),
    ("current_period_end", True), ("current_period_end", NOW),
    ("current_period_end", NOW - 1), ("current_period_end", float("inf")),
    ("current_period_end", 253402300800), ("current_period_end", NOW + 86400.0),
])
def test_period_must_establish_current_entitlement_with_strict_bounded_integers(field, wrong):
    value = snapshot()
    value[field] = wrong
    with pytest.raises(CapRestorationEntitlementError):
        validate(value)


@pytest.mark.parametrize("cancel_at", [True, False, NOW, NOW - 1, NOW + 1, NOW + 86401,
                                       NOW + 86400.0, str(NOW + 86400), float("inf")])
def test_scheduled_cancellation_requires_null_or_the_exact_future_period_end(cancel_at):
    value = snapshot()
    value.update(cancel_at_period_end=True, cancel_at=cancel_at)
    with pytest.raises(CapRestorationEntitlementError):
        validate(value)


@pytest.mark.parametrize("items", [None, [], {}, {"data": []},
    {"object": "list", "has_more": False, "data": []},
    {"object": "list", "has_more": False, "data": (snapshot()["items"]["data"][0],)},
    {"object": "list", "has_more": True, "data": snapshot()["items"]["data"]},
    {"object": "list", "has_more": 0, "data": snapshot()["items"]["data"]},
    {"object": "list", "has_more": False, "data": snapshot()["items"]["data"] * 2},
    {"object": "list", "has_more": False, "data": [None]},
    {"object": "list", "has_more": False, "total_count": 2, "data": snapshot()["items"]["data"]},
    {"object": "list", "has_more": False, "total_count": True, "data": snapshot()["items"]["data"]},
])
def test_only_a_complete_single_item_list_is_supported(items):
    value = snapshot()
    value["items"] = items
    with pytest.raises(CapRestorationEntitlementError):
        validate(value)


@pytest.mark.parametrize("field", ["object", "id", "subscription", "quantity", "price"])
def test_missing_item_binding_refuses(field):
    value = snapshot()
    del value["items"]["data"][0][field]
    with pytest.raises(CapRestorationEntitlementError):
        validate(value)


@pytest.mark.parametrize("field,wrong", [
    ("object", "price"), ("id", "si_"), ("id", "si_restore/foreign"),
    ("id", "si_restore\n"), ("id", "si_" + "a" * 97),
    ("subscription", "sub_other"), ("subscription", {"id": "sub_restore"}),
    ("quantity", True), ("quantity", 0), ("quantity", 2), ("quantity", 1.0), ("quantity", "1"),
    ("price", "price_other"), ("price", None), ("price", {"id": "price_plan"}),
    ("price", {"object": "price", "id": "price_plan", "livemode": True}),
    ("price", {"object": "price", "id": "price_plan", "livemode": 0}),
    ("price", {"object": "product", "id": "price_plan", "livemode": False}),
    ("price", {"object": "price", "id": "price_plan", "livemode": False, "deleted": True}),
    ("deleted", True), ("current_period_start", NOW - 3599),
    ("current_period_start", NOW - 3600.0), ("current_period_end", NOW + 86399),
    ("current_period_end", NOW + 86400.0), ("current_period_end", True),
])
def test_item_price_quantity_and_optional_periods_must_agree(field, wrong):
    value = snapshot()
    value["items"]["data"][0][field] = wrong
    with pytest.raises(CapRestorationEntitlementError):
        validate(value)


@pytest.mark.parametrize("config", [
    {"expected_price_id": "price_"}, {"expected_price_id": "price_plan\n"},
    {"expected_price_id": "price_" + "a" * 97}, {"expected_price_id": None},
    {"expected_livemode": 0}, {"expected_livemode": "false"}, {"expected_livemode": None},
    {"now": True}, {"now": False}, {"now": NOW + 0.0}, {"now": 0}, {"now": -1},
    {"now": str(NOW)}, {"now": float("nan")}, {"now": float("inf")}, {"now": 253402300800},
])
def test_configuration_and_observation_time_are_not_provider_claims(config):
    with pytest.raises(CapRestorationEntitlementError):
        validate(**config)


@pytest.mark.parametrize("binding", [None, {},
    {"tenant_id": "usr-cap-restore", "customer_id": "cus_restore", "subscription_id": "sub_restore"},
    StripeBinding("usr-cap-restore", "cus_other", "sub_restore"),
    StripeBinding("usr-cap-restore", "cus_restore", "sub_other"),
])
def test_the_caller_must_supply_the_exact_typed_server_binding(binding):
    with pytest.raises(CapRestorationEntitlementError):
        validate(binding=binding)


@pytest.mark.parametrize("field,wrong", [("tenant_id", ""), ("customer_id", "cus_restore?foreign"),
                                        ("subscription_id", "sub_restore\n")])
def test_forged_frozen_binding_is_revalidated(field, wrong):
    binding = deepcopy(BINDING)
    object.__setattr__(binding, field, wrong)
    with pytest.raises(CapRestorationEntitlementError):
        validate(binding=binding)


@pytest.mark.parametrize("value", [None, False, 1, [], "PRIVATE provider response"])
def test_non_object_snapshots_fail_with_one_sanitized_error(value):
    with pytest.raises(CapRestorationEntitlementError) as error:
        validate_restoration_entitlement(value, BINDING, **CONFIG)
    assert str(error.value) == "Current supported Stripe entitlement could not be established"
