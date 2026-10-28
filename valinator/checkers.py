"""Local validation and explicit test doubles. No external model calls."""

import asyncio
from collections.abc import Awaitable, Callable

from jsonschema import Draft202012Validator

from valinator.models import CheckerResult, CheckRequest, Finding, Policy, Stage
from valinator.json_data import parse_json
from valinator.formats import FORMAT_CHECKER
from valinator.business import cross_field_check
from valinator.completeness import completeness_check
from valinator.text_security import text_security_check
from valinator.secrets_check import build_secret_checker

Checker = Callable[[CheckRequest, Policy, Stage], Awaitable[CheckerResult]]


SCHEMA_MESSAGES = {
    "required": "A required field is missing.",
    "type": "A value has the wrong data type.",
    "additionalProperties": "An unexpected field is present.",
    "enum": "A value is outside the allowed choices.",
    "const": "A value does not match the required constant.",
    "format": "A value does not match a supported format.",
    "pattern": "A string does not match its configured pattern.",
    "minLength": "A string is shorter than the configured minimum.",
    "maxLength": "A string exceeds the configured maximum length.",
    "minimum": "A number is below the configured minimum.",
    "maximum": "A number exceeds the configured maximum.",
    "exclusiveMinimum": "A number is not above the configured lower bound.",
    "exclusiveMaximum": "A number is not below the configured upper bound.",
    "multipleOf": "A number is not a configured multiple.",
    "minItems": "An array has fewer items than required.",
    "maxItems": "An array has too many items.",
    "uniqueItems": "An array contains duplicate items.",
}


async def schema_check(request: CheckRequest, policy: Policy, stage: Stage) -> CheckerResult:
    schema = policy.input_schema if stage == Stage.INPUT else policy.output_schema
    if schema is None or request.content_format != "json":
        return CheckerResult(checker_id="schema", checker_version="2", status="skipped")
    try:
        payload = parse_json(request.content)
    except (ValueError, RecursionError):
        return CheckerResult(checker_id="schema", checker_version="2", status="flagged",
                             findings=(Finding(rule_id="schema.invalid_json", category="structure",
                                               severity="high", explanation="Content is malformed JSON or exceeds supported strict-JSON limits."),))
    error = next(Draft202012Validator(schema, format_checker=FORMAT_CHECKER).iter_errors(payload), None)
    valid = error is None
    # Never use ValidationError.message, paths, values, schema text or field names.
    reason = SCHEMA_MESSAGES.get(error.validator, "The structured payload does not satisfy the configured schema.") if error else ""
    return CheckerResult(
        checker_id="schema", checker_version="2", status="passed" if valid else "flagged",
        findings=() if valid else (Finding(
            rule_id="schema.invalid", category="structure", severity="high",
            explanation=reason,
        ),),
    )


schema_check.checker_version = "2"


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


def default_checkers(enable_pii: bool = False, enable_azure: bool = False) -> dict[str, Checker]:
    checkers = {"schema": schema_check, "safety": simulated_checker("safety"),
                "bias": simulated_checker("bias"), "cross_field": cross_field_check,
                "secrets": build_secret_checker(), "completeness": completeness_check, "text_security": text_security_check}
    if enable_pii:
        from valinator.pii import build_pii_checker
        checkers["pii"] = build_pii_checker()
    if enable_azure:
        from valinator.azure_prompt_shields import from_environment
        checkers["azure_prompt_shields"] = from_environment()
    return checkers
