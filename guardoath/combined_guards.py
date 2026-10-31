"""Unified bounded evaluator for GuardOath lexical indicators.

No tool execution. Host is responsible for enforcing decisions.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from . import semantic_guards, agent_extended_guards, data_agent_guards, agent_workflow_guards

Mode = Literal["observe", "enforce"]

@dataclass(frozen=True)
class GuardDecision:
    mode: Mode
    blocked: bool
    rule_ids: tuple[str, ...]
    severity: str

def evaluate(content: str, *, mode: Mode = "observe") -> GuardDecision:
    if mode not in ("observe", "enforce"):
        raise ValueError("mode must be observe or enforce")
    if not isinstance(content, str):
        raise TypeError("content must be str")
    if len(content) > semantic_guards.MAX_CHARS:
        raise ValueError("content exceeds maximum character budget")
    found = (
        semantic_guards.inspect_text(content)
        + agent_extended_guards.inspect_extended(content)
        + data_agent_guards.inspect_text(content)
        + agent_workflow_guards.inspect_text(content)
    )
    ids = tuple(dict.fromkeys(item.rule_id for item in found))
    severity = "high" if any(item.severity == "high" for item in found) else ("medium" if found else "none")
    return GuardDecision(mode=mode, blocked=bool(ids) and mode == "enforce", rule_ids=ids, severity=severity)
