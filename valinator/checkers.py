"""Local validation and explicit test doubles. No external model calls."""

import asyncio
import json
from collections.abc import Awaitable, Callable

from jsonschema import Draft202012Validator

from valinator.models import CheckerResult, CheckRequest, Finding, Policy, Stage

Checker = Callable[[CheckRequest, Policy, Stage], Awaitable[CheckerResult]]


async def schema_check(request: CheckRequest, policy: Policy, stage: Stage) -> CheckerResult:
    schema = policy.input_schema if stage == Stage.INPUT else policy.output_schema
    if schema is None or request.content_format != "json":
        return CheckerResult(checker_id="schema", checker_version="1", status="skipped")
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("Nonfinite JSON number")

    try:
        payload = json.loads(request.content, object_pairs_hook=object_pairs,
                             parse_constant=reject_constant)
    except (ValueError, RecursionError):
        return CheckerResult(checker_id="schema", checker_version="1", status="flagged",
                             findings=(Finding(rule_id="schema.invalid_json", category="structure",
                                               severity="high", explanation="Content is not valid strict JSON."),))
    valid = Draft202012Validator(schema).is_valid(payload)
    return CheckerResult(
        checker_id="schema", checker_version="1", status="passed" if valid else "flagged",
        findings=() if valid else (Finding(
            rule_id="schema.invalid", category="structure", severity="high",
            explanation="The structured payload does not satisfy the configured schema.",
        ),),
    )


def simulated_checker(kind: str) -> Checker:
    """Exact synthetic markers exercise orchestration; they are not detectors."""
    async def run(request: CheckRequest, policy: Policy, stage: Stage) -> CheckerResult:
        marker = request.content
        if marker == f"[SIMULATE:{kind}:ERROR]":
            raise RuntimeError("Synthetic provider failure")
        if marker == f"[SIMULATE:{kind}:TIMEOUT]":
            await asyncio.sleep(policy.timeout_ms / 1000 + 0.1)
        flagged = marker == f"[SIMULATE:{kind}:FLAG]"
        return CheckerResult(
            checker_id=kind, checker_version="simulator-1", simulated=True,
            status="flagged" if flagged else "passed",
            findings=(Finding(
                rule_id=f"{kind}.synthetic", category=kind, severity="medium",
                explanation="Synthetic test finding; no real content assessment was performed.",
            ),) if flagged else (),
        )
    # Registration metadata survives an adapter raising before it can return.
    setattr(run, "simulated", True)
    setattr(run, "checker_version", "simulator-1")
    return run


def default_checkers() -> dict[str, Checker]:
    return {"schema": schema_check, "safety": simulated_checker("safety"),
            "bias": simulated_checker("bias")}
