"""Tests for local agent runtime reservation primitive (not distributed enforcement)."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from guardoath.agent_budgets import AgentBudget, AgentLimits, BudgetExceeded


def test_boundary_and_depth():
    budget = AgentBudget(AgentLimits(max_steps=2, max_depth=1, max_calls=1))
    budget.reserve(depth=0, calls=1)
    budget.reserve(depth=1)
    assert budget.snapshot() == {"steps": 2, "calls": 1}
    with pytest.raises(BudgetExceeded):
        budget.reserve(depth=1)
    with pytest.raises(BudgetExceeded):
        budget.reserve(depth=2)
    assert budget.snapshot() == {"steps": 2, "calls": 1}


@pytest.mark.parametrize("value", [-1, 1.5, True, "2"])
def test_invalid_depth_never_changes_state(value):
    budget = AgentBudget(AgentLimits())
    with pytest.raises(ValueError):
        budget.reserve(depth=value)
    assert budget.snapshot() == {"steps": 0, "calls": 0}


def test_concurrent_reservations_are_atomic():
    budget = AgentBudget(AgentLimits(max_steps=20, max_calls=20))
    def attempt(_):
        try:
            budget.reserve(depth=0, calls=1)
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, range(100)))
    assert sum(results) == 20
    assert budget.snapshot() == {"steps": 20, "calls": 20}


def test_invalid_limits_rejected():
    with pytest.raises(ValueError):
        AgentLimits(max_steps=0)
    with pytest.raises(ValueError):
        AgentLimits(max_depth=-1)
    with pytest.raises(ValueError):
        AgentLimits(max_calls=0)
