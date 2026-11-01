"""Pure agent-action authorization decision helper.

Does not execute tools or authenticate identities. A trusted host supplies the
verified principal, policy, and approval evidence; missing evidence fails closed.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

Decision = Literal["allow", "flag", "block"]
SENSITIVE = frozenset({"delete", "publish", "export", "change_permissions", "disable_control"})
READ_ONLY = frozenset({"read", "list", "describe"})

@dataclass(frozen=True)
class ActionContext:
    tenant_id: str
    principal_tenant_id: str
    action: str
    resource_tenant_id: str
    authenticated: bool
    permitted_actions: frozenset[str]
    approved: bool = False
    approval_required: bool = True
    signals: tuple[str, ...] = ()
    environment: Literal["production", "staging", "development"] = "production"

@dataclass(frozen=True)
class DecisionResult:
    action: Decision
    reason_codes: tuple[str, ...]
    signal_ids: tuple[str, ...]

def decide(context: ActionContext) -> DecisionResult:
    """Return a deterministic pre-dispatch recommendation with all reasons.

    Signals alone never establish identity, approval, or authorization.
    Caller must enforce the returned block before invoking an external tool.
    """
    if not isinstance(context, ActionContext):
        raise TypeError("expected ActionContext")
    if not all(isinstance(v, str) and 0 < len(v) <= 128 for v in
               (context.tenant_id, context.principal_tenant_id,
                context.resource_tenant_id, context.action)):
        return DecisionResult("block", ("invalid_context",), ())
    signals = tuple(dict.fromkeys(s for s in context.signals
                                  if isinstance(s, str) and 0 < len(s) <= 128))[:100]
    reasons = []
    if not context.authenticated:
        reasons.append("unauthenticated")
    if context.principal_tenant_id != context.tenant_id or context.resource_tenant_id != context.tenant_id:
        reasons.append("tenant_mismatch")
    if context.action not in context.permitted_actions:
        reasons.append("permission_denied")
    if context.approval_required and context.action in SENSITIVE and not context.approved:
        reasons.append("approval_missing")
    if context.action not in SENSITIVE and context.action not in READ_ONLY:
        reasons.append("unknown_action")
    if reasons:
        return DecisionResult("block", tuple(reasons), signals)
    if signals:
        return DecisionResult("flag", ("security_indicators",), signals)
    return DecisionResult("allow", (), ())
