"""Declarative comparisons of submitted fields. No expressions or eval."""

from datetime import date
from decimal import Decimal
import operator
import re

from valinator.json_data import parse_json
from valinator.models import CheckerResult, Finding

OPERATORS = {"lt": operator.lt, "le": operator.le, "eq": operator.eq,
             "ge": operator.ge, "gt": operator.gt, "ne": operator.ne}


def resolve(value, pointer):
    for segment in pointer[1:].split("/"):
        name = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            value = value[name]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", name):
            value = value[int(name)]
        else:
            raise ValueError("Missing comparison field")
    return value


def typed(value, kind):
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Comparison requires a number")
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("Comparison requires a finite number")
        return result
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Comparison requires an ISO date")
    return date.fromisoformat(value)


async def cross_field_check(request, policy, stage):
    comparisons = [rule for rule in policy.comparisons if stage in rule.stages]
    if not comparisons or request.content_format != "json":
        return CheckerResult(checker_id="cross_field", checker_version="1", status="skipped")
    try:
        payload = parse_json(request.content)
    except (ValueError, RecursionError):
        return CheckerResult(checker_id="cross_field", checker_version="1", status="flagged", findings=(
            Finding(rule_id="business.invalid_json", category="business", severity="high",
                    explanation="Field comparison requires valid bounded JSON."),))
    findings = []
    for rule in comparisons:
        try:
            left, right = (typed(resolve(payload, path), rule.value_type) for path in (rule.left, rule.right))
            valid = OPERATORS[rule.operator](left, right)
        except (ValueError, KeyError, IndexError, TypeError, OverflowError):
            valid = False
        if not valid:
            findings.append(Finding(rule_id=f"business.{rule.rule_id}", category="business", severity="medium",
                                    explanation=f"Configured field comparison '{rule.rule_id}' failed or required values are missing or invalid."))
    return CheckerResult(checker_id="cross_field", checker_version="1", status="flagged" if findings else "passed",
                         findings=tuple(findings))
