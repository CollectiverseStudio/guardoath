"""Local deterministic GuardOath checker; the Engine owns enforcement.

Lexical indicators are signals, not proof of malicious intent.
"""
from __future__ import annotations

from . import semantic_guards, agent_extended_guards, data_agent_guards, agent_workflow_guards

def _signals(content: str):
    if not isinstance(content, str):
        raise TypeError("content must be text")
    if len(content) > semantic_guards.MAX_CHARS:
        raise ValueError("content exceeds maximum character budget")
    return (
        semantic_guards.inspect_text(content)
        + agent_extended_guards.inspect_extended(content)
        + data_agent_guards.inspect_text(content)
        + agent_workflow_guards.inspect_text(content)
    )

async def lexical_guards_check(request, policy, stage):
    from guardoath.models import CheckerResult, Finding
    if policy.mode not in ("observe", "enforce"):
        raise ValueError("unsupported policy mode")
    signals = _signals(request.content)
    unique = {signal.rule_id: signal for signal in signals}
    findings = tuple(
        Finding(rule_id=rule_id, category="lexical_security",
                severity="high" if signal.severity == "high" else "medium",
                explanation="A lexical security indicator matched; contextual review may be required.")
        for rule_id, signal in unique.items()
    )
    return CheckerResult(checker_id="lexical_guards", checker_version="2",
                         status="flagged" if findings else "passed", findings=findings)

lexical_guards_check.checker_version = "2"
