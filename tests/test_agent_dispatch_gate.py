"""Agent dispatch gate composition tests. No tool execution is performed."""
from concurrent.futures import ThreadPoolExecutor

from valinator.agent_authorization import Principal
from valinator.agent_budgets import AgentBudget, AgentLimits
from valinator.agent_dispatch_gate import AgentDispatchGate, DispatchRequest
from valinator.workflow_guard import TransitionPolicy


def fixture():
    budget = AgentBudget(AgentLimits(max_steps=3, max_calls=3, max_depth=2))
    gate = AgentDispatchGate(TransitionPolicy({
        "created": ("paid",), "paid": ("shipped",), "shipped": (),
    }), budget)
    principal = Principal("tenant-a", frozenset({"shipments:write"}))
    return gate, budget, principal


def request(**overrides):
    args = dict(tenant_id="tenant-a", permission="shipments:write",
                current_state="paid", target_state="shipped", depth=1)
    args.update(overrides)
    return DispatchRequest(**args)


def test_authorized_dispatch_reserves_budget():
    gate, budget, principal = fixture()
    decision = gate.assess(principal, request())
    assert decision.allowed and decision.reason == "approved_for_host_recheck"
    assert budget.snapshot() == {"steps": 1, "calls": 1}


def test_denials_do_not_consume_budget():
    gate, budget, principal = fixture()
    assert gate.assess(principal, request(tenant_id="tenant-b")).reason == "not_authorized"
    assert gate.assess(principal, request(permission="shipments:delete")).reason == "not_authorized"
    assert gate.assess(principal, request(current_state="created")).reason == "transition_not_allowed"
    assert budget.snapshot() == {"steps": 0, "calls": 0}


def test_depth_and_total_budget_denials():
    gate, budget, principal = fixture()
    assert gate.assess(principal, request(depth=3)).reason == "execution_budget_denied"
    assert [gate.assess(principal, request()).allowed for _ in range(4)] == [
        True, True, True, False]
    assert budget.snapshot() == {"steps": 3, "calls": 3}


def test_concurrent_gate_reservations_are_bounded():
    gate, budget, principal = fixture()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: gate.assess(principal, request()).allowed,
                                range(30)))
    assert sum(results) == 3
    assert budget.snapshot() == {"steps": 3, "calls": 3}
