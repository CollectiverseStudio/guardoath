"""Fail-closed pre-dispatch decision gateway for an agent host.

This module validates authorization, workflow transitions and in-process budgets.
It never invokes a tool, authenticates a user or changes durable workflow state.
"""
from dataclasses import dataclass

from valinator.agent_authorization import Principal, authorize
from valinator.agent_budgets import AgentBudget, BudgetExceeded
from valinator.workflow_guard import TransitionPolicy, validate_transition


@dataclass(frozen=True)
class DispatchRequest:
    tenant_id: str
    permission: str
    current_state: str
    target_state: str
    depth: int


@dataclass(frozen=True)
class DispatchDecision:
    allowed: bool
    reason: str


class AgentDispatchGate:
    def __init__(self, transitions: TransitionPolicy, budget: AgentBudget):
        self.transitions = transitions
        self.budget = budget

    def assess(self, principal: Principal, request: DispatchRequest) -> DispatchDecision:
        if not authorize(principal, tenant_id=request.tenant_id,
                         permission=request.permission):
            return DispatchDecision(False, "not_authorized")
        transition = validate_transition(
            self.transitions, request.current_state, request.target_state)
        if not transition.allowed:
            return DispatchDecision(False, transition.reason)
        try:
            self.budget.reserve(depth=request.depth, calls=1)
        except (BudgetExceeded, ValueError):
            return DispatchDecision(False, "execution_budget_denied")
        return DispatchDecision(True, "approved_for_host_recheck")
