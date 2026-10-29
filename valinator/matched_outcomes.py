"""Local comparison of submitted matched decisions; no free-text bias classifier."""
import json
from decimal import Decimal, DecimalException

from valinator.business import exact_number
from valinator.json_data import parse_json
from valinator.models import CheckerResult, Finding

CHECKER_ID = 'matched_outcomes'
VERSION = '1'
MAX_RECORDS = 20
MAX_FACTORS = 20


def fingerprint(value):
    """Typed JSON equivalence; exact numbers, no bool/number or string coercion."""
    if value is None:
        return ('null',)
    if type(value) is bool:
        return ('boolean', value)
    if isinstance(value, (int, Decimal)):
        return ('number', Decimal(value))
    if isinstance(value, str):
        return ('string', value)
    if isinstance(value, list):
        return ('array', tuple(fingerprint(v) for v in value))
    if isinstance(value, dict):
        return ('object', tuple((k, fingerprint(v)) for k, v in sorted(value.items())))
    raise ValueError('Unsupported JSON value')


def contains_attribute(value, names):
    if isinstance(value, dict):
        return any(k.casefold() in names or contains_attribute(v, names) for k, v in value.items())
    if isinstance(value, list):
        return any(contains_attribute(v, names) for v in value)
    return False


def records(text, configuration):
    # Reuse strict JSON guards, then preserve decimal float tokens exactly. No
    # precision/context rounding, semantic extraction or model call is performed.
    parse_json(text)
    data = json.loads(text, parse_float=exact_number)
    if not isinstance(data, dict) or set(data) != {'scenarios'}:
        raise ValueError('Expected scenarios envelope')
    rows = data['scenarios']
    if not isinstance(rows, list) or not 2 <= len(rows) <= MAX_RECORDS:
        raise ValueError('Scenario count limit')
    names = set(configuration.attributes)
    normalized_names = {n.casefold() for n in names}
    prepared = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'factors', 'attributes', 'decision'}:
            raise ValueError('Expected complete scenario')
        factors, attributes, decision = row['factors'], row['attributes'], row['decision']
        if (not isinstance(factors, dict) or not 1 <= len(factors) <= MAX_FACTORS
                or contains_attribute(factors, normalized_names)):
            raise ValueError('Missing or overlapping decision factors')
        if not isinstance(attributes, dict) or set(attributes) != names:
            raise ValueError('Attribute configuration mismatch')
        if any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in attributes.values()):
            raise ValueError('Invalid attribute value')
        if configuration.outcome_type == 'string':
            valid = isinstance(decision, str) and bool(decision.strip()) and len(decision) <= 1_000
        elif configuration.outcome_type == 'boolean':
            valid = type(decision) is bool
        else:
            valid = type(decision) is int or isinstance(decision, Decimal)
        if not valid:
            raise ValueError('Invalid decision value')
        prepared.append((fingerprint(factors), fingerprint(attributes), fingerprint(decision)))
    return prepared


async def matched_outcomes_check(request, policy, stage):
    def result(status, code=None, findings=()):
        return CheckerResult(checker_id=CHECKER_ID, checker_version=VERSION, status=status,
                             error_code=code, findings=findings)
    if policy.matched_outcomes is None or request.content_format != 'json':
        return result('skipped', 'matched_outcomes_json_required')
    try:
        rows = records(request.content, policy.matched_outcomes)
    except (ValueError, TypeError, RecursionError, DecimalException):
        return result('error', 'matched_outcomes_invalid_records')
    comparable = different = 0
    covered = set()
    for left in range(len(rows)):
        for right in range(left + 1, len(rows)):
            a, b = rows[left], rows[right]
            if a[0] == b[0] and a[1] != b[1]:
                comparable += 1
                covered.update((left, right))
                different += a[2] != b[2]
    # Do not hide unassessed records behind the subset that happened to match.
    if len(covered) != len(rows):
        return result('error', 'matched_outcomes_insufficient_comparisons')
    if different:
        return result('flagged', findings=(Finding(rule_id='bias.matched_outcome_difference',
            category='bias', severity='medium', explanation=(
                f'Submitted outcomes differ in {different} of {comparable} comparable scenario pairs. '
                'Declared decision factors match and configured attributes differ. '
                'Review the omitted context and decision process; this is a consistency signal, not proof of discrimination.')),))
    return result('passed')


matched_outcomes_check.checker_version = VERSION
