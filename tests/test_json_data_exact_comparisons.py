"""Strict JSON comparison decoder retains original guards in one parse."""
import asyncio
from decimal import Decimal

import pytest

from guardoath.business import cross_field_check, exact_number, typed
from guardoath.json_data import parse_json
from guardoath.models import CheckRequest, FieldComparison, Policy, Rule, Stage


@pytest.mark.parametrize("content", [
    '{"x":1e500}', '{"x":NaN}', '{"x":Infinity}',
    '{"x":1,"x":2}', '{"x":1,}', '{"x":' + "1" * 129 + '}',
    "[" * 33 + "0" + "]" * 33, '{"x":"\\ud800"}',
])
def test_lossless_cross_field_parser_rejects_all_original_strict_boundaries(content):
    with pytest.raises((ValueError, RecursionError)):
        parse_json(content)
    with pytest.raises((ValueError, RecursionError)):
        parse_json(content, float_converter=exact_number)


def test_cross_field_parse_keeps_decimals_exact_without_coercing_booleans():
    raw = '{"a":0.30000000000000004,"b":0.3,"c":true,"d":1e-500}'
    result = parse_json(raw, float_converter=exact_number)
    assert result["a"] == Decimal("0.30000000000000004")
    assert result["b"] == Decimal("0.3")
    assert result["d"] == Decimal("1e-500")
    assert type(result["c"]) is bool
    with pytest.raises(ValueError):
        typed(result["c"], "number")
    with pytest.raises(ValueError):
        typed(result["c"], "date")


def test_cross_field_runs_one_strict_parse_and_flags_boolean_date(monkeypatch):
    import guardoath.json_data as json_data
    calls = []
    original = json_data.json.loads

    def count_loads(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(json_data.json, "loads", count_loads)
    rule = FieldComparison(
        rule_id="inspection_date", left="/inspected_date", right="/installed_date",
        operator="ge", value_type="date")
    policy = Policy(application_id="demo", version="strict-once",
                    rules=(Rule(checker_id="cross_field", on_finding="block"),),
                    comparisons=(rule,))
    request = CheckRequest(application_id="demo", policy_version=policy.version,
                           content='{"inspected_date":true,"installed_date":"2026-11-01"}',
                           content_format="json")
    result = asyncio.run(cross_field_check(request, policy, Stage.INPUT))
    assert len(calls) == 1
    assert result.status == "flagged"
    assert {finding.rule_id for finding in result.findings} == {"business.inspection_date"}
    assert "missing or invalid" in result.findings[0].explanation
