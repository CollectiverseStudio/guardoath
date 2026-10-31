"""Local deterministic GuardOath checker; the Engine owns enforcement.

Lexical indicators are signals, not proof of malicious intent.
Registered in guardoath.checkers.default_checkers as "lexical_guards"; tenant records turn it on with
check_lexical_guards. JSON content is checked string by string (keys and values, after JSON decoding),
so escaped characters such as \\u0074 cannot hide a command.
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

CHECKER_VERSION = "3"


async def lexical_guards_check(request, policy, stage):
    from guardoath.json_data import parse_json
    from guardoath.models import CheckerResult, Finding
    from guardoath.text_security import texts
    if policy.mode not in ("observe", "enforce"):
        raise ValueError("unsupported policy mode")
    if request.content_format == "json":
        try:
            parts = tuple(texts(parse_json(request.content)))
        except (ValueError, RecursionError):
            return CheckerResult(checker_id="lexical_guards", checker_version=CHECKER_VERSION, status="error",
                                 error_code="invalid_json_for_lexical_inspection")
    else:
        parts = (request.content,)
    unique = {}
    for part in parts:
        for signal in _signals(part):
            unique.setdefault(signal.rule_id, signal)
    findings = tuple(
        Finding(rule_id=rule_id, category="lexical_security",
                severity="high" if signal.severity == "high" else "medium",
                explanation="A lexical security indicator matched; contextual review may be required.")
        for rule_id, signal in unique.items()
    )
    return CheckerResult(checker_id="lexical_guards", checker_version=CHECKER_VERSION,
                         status="flagged" if findings else "passed", findings=findings)

lexical_guards_check.checker_version = CHECKER_VERSION
