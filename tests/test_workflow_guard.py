"""UC104 local transition guard tests; not an atomic workflow database."""
import pytest

from guardoath.workflow_guard import TransitionPolicy, validate_transition


def policy():
    return TransitionPolicy({
        "created": ("paid", "cancelled"),
        "paid": ("shipped", "refunded"),
        "shipped": ("delivered",),
        "delivered": (),
        "cancelled": (),
        "refunded": (),
    })


def test_shipping_before_payment_is_denied():
    assert validate_transition(policy(), "created", "shipped").allowed is False
    assert validate_transition(policy(), "created", "shipped").reason == "transition_not_allowed"


def test_authorized_sequence_and_no_shortcuts():
    p = policy()
    assert validate_transition(p, "created", "paid").allowed
    assert validate_transition(p, "paid", "shipped").allowed
    assert validate_transition(p, "shipped", "delivered").allowed
    assert not validate_transition(p, "created", "delivered").allowed
    assert not validate_transition(p, "shipped", "paid").allowed


def test_unknown_states_fail_closed():
    p = policy()
    assert validate_transition(p, "unknown", "paid").reason == "unknown_state"
    assert not validate_transition(p, "paid", "unknown").allowed


def test_invalid_transition_graph_rejected():
    with pytest.raises(ValueError):
        TransitionPolicy({"created": ("missing",)})
    with pytest.raises(ValueError):
        TransitionPolicy({"created": ("created", "created")})
    with pytest.raises(ValueError):
        TransitionPolicy({})
