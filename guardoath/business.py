"""Declarative comparisons of submitted fields. No expressions or eval."""

from datetime import date
from decimal import Decimal, DecimalException
import json
import operator
import re

from guardoath.json_data import parse_json
from guardoath.models import CheckerResult, Finding

OPERATORS = {"lt": operator.lt, "le": operator.le, "eq": operator.eq,
             "ge": operator.ge, "gt": operator.gt, "ne": operator.ne}

RELATION_LABELS = {"lt":"less than", "le":"less than or equal to", "eq":"equal to",
                   "ge":"greater than or equal to", "gt":"greater than", "ne":"different from"}


# Stable across supported Python/Decimal runtimes. No exponent arithmetic is run.
MAX_COMPARISON_EXPONENT = 1_000_000


def exact_number(token):
    exponent = token.lower().partition("e")[2]
    if exponent and abs(int(exponent)) > MAX_COMPARISON_EXPONENT:
        raise ValueError("Comparison exponent limit")
    return Decimal(token)


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
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
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
        return CheckerResult(checker_id="cross_field", checker_version="2", status="skipped")
    try:
        # Preserve the strict parser's duplicate/size/depth/finite-number guards,
        # then decode the same canonical text with exact decimal float tokens.
        parse_json(request.content)
        payload = json.loads(request.content, parse_float=exact_number)
    except (ValueError, RecursionError, DecimalException):
        return CheckerResult(checker_id="cross_field", checker_version="2", status="flagged", findings=(
            Finding(rule_id="business.invalid_json", category="business", severity="high",
                    explanation="Field comparison requires valid bounded JSON."),))
    findings = []
    for rule in comparisons:
        try:
            left, right = (typed(resolve(payload, path), rule.value_type) for path in (rule.left, rule.right))
            valid = OPERATORS[rule.operator](left, right)
        except (ValueError, KeyError, IndexError, TypeError, OverflowError, DecimalException):
            valid = False
            reason = f"Configured comparison '{rule.rule_id}' has a missing or invalid {rule.value_type} value."
        else:
            reason = f"Configured {rule.value_type} comparison '{rule.rule_id}' requires the left value to be {RELATION_LABELS[rule.operator]} the right value."
        if not valid:
            findings.append(Finding(rule_id=f"business.{rule.rule_id}", category="business", severity="medium",
                                    explanation=reason))
    return CheckerResult(checker_id="cross_field", checker_version="2", status="flagged" if findings else "passed",
                         findings=tuple(findings))


cross_field_check.checker_version = "2"
