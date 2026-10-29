"""Validate declared tool names/arguments only. Never execute or authorize tools."""
import json
from decimal import Decimal, DecimalException
from pydantic import Field, ValidationError
from valinator.business import exact_number
from jsonschema import Draft202012Validator

from valinator.formats import FORMAT_CHECKER
from valinator.json_data import parse_json
from valinator.models import Contract, CheckerResult, Finding


class ProposedToolCall(Contract):
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,49}$', strict=True)
    arguments: dict


class ProposedToolCalls(Contract):
    tool_calls: tuple[ProposedToolCall, ...] = Field(max_length=10)


def round_trip_number(token):
    value = float(token)
    if Decimal(str(value)) != exact_number(token):
        raise ValueError('Lossy tool argument number')
    return value


async def tool_calls_check(request, policy, stage):
    def result(status, code=None, findings=()):
        return CheckerResult(checker_id='tool_calls', checker_version='1', status=status,
                             error_code=code, findings=findings)
    if policy.tool_calls is None or request.content_format != 'json':
        return result('skipped', 'tool_calls_json_required')
    try:
        parse_json(request.content)
        raw = json.loads(request.content, parse_float=round_trip_number)
        data = ProposedToolCalls.model_validate(raw)
    except (ValueError, RecursionError, ValidationError, DecimalException):
        return result('error', 'tool_calls_invalid_document')
    allowed = {tool.name: tool.arguments_schema for tool in policy.tool_calls.tools}
    unknown = invalid = False
    for call in data.tool_calls:
        if call.name not in allowed:
            unknown = True
        else:
            # Trusted startup schemas, with the same local-reference/format
            # validation as the ordinary schema checker. Never expose messages.
            error = next(Draft202012Validator(allowed[call.name], format_checker=FORMAT_CHECKER).iter_errors(call.arguments), None)
            invalid = invalid or error is not None
    findings = []
    if unknown:
        findings.append(Finding(rule_id='actions.unknown_tool', category='actions', severity='high',
            explanation='At least one proposed tool is outside the configured allowlist. No tool was executed or authorized.'))
    if invalid:
        findings.append(Finding(rule_id='actions.invalid_arguments', category='actions', severity='high',
            explanation='At least one allowed tool has arguments that do not satisfy its configured schema. No tool was executed or authorized.'))
    return result('flagged' if findings else 'passed', findings=tuple(findings))


tool_calls_check.checker_version = '1'
