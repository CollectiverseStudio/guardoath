"""Contract tests for the proposed 30 field-comparison use cases.

This evidence covers current declarative cross_field behavior, not tenant catalog
registration, source-of-truth validation, or production enablement.
"""
import asyncio
import json
from pathlib import Path

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import CheckRequest, FieldComparison, Policy, Rule, Stage


INVENTORY = json.loads(
    (Path(__file__).resolve().parents[1] / "docs/field-comparisons-rule-inventory.json").read_text(encoding="utf-8")
)
CASES = INVENTORY["rules"]


def _check(case, payload, stage=Stage.INPUT):
    comparison = FieldComparison(**case["comparison"])
    policy = Policy(
        application_id="demo", version="field-comparisons-test-v1",
        rules=(Rule(checker_id="cross_field", on_finding="block"),),
        comparisons=(comparison,),
    )
    engine = Engine([policy], default_checkers())
    request = CheckRequest(
        application_id="demo", policy_version=policy.version,
        content=json.dumps(payload), content_format="json",
    )
    return asyncio.run(engine.check(request, stage))


def test_inventory_has_30_unique_non_deployed_comparison_templates():
    assert INVENTORY["category"] == "Field comparisons"
    assert INVENTORY["check_id"] == "cross_field"
    assert INVENTORY["status"] == "proposed_not_enabled"
    assert INVENTORY["count"] == len(CASES) == 30
    assert len({case["id"] for case in CASES}) == len(CASES)
    assert len({case["rule_id"] for case in CASES}) == len(CASES)
    assert {case["comparison"]["operator"] for case in CASES} == {"lt", "le", "eq", "ge", "gt", "ne"}
    assert {case["comparison"]["value_type"] for case in CASES} == {"number", "date"}
    for case in CASES:
        assert case["status"] == "proposed"
        assert case["expected_finding"] == "business." + case["rule_id"]
        assert case["rule_id"] == case["comparison"]["rule_id"]
        assert case["comparison"]["stages"] == ["input", "output"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_documented_positive_detects_its_exact_original_rule(case, stage):
    result = _check(case, case["positive"], stage)
    assert result.evaluation_status == "complete"
    assert {finding.rule_id for finding in result.findings} == {case["expected_finding"]}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_documented_benign_lookalike_is_not_flagged(case, stage):
    result = _check(case, case["negative"], stage)
    assert result.evaluation_status == "complete"
    assert result.findings == ()
    assert result.checker_results[0].status == "passed"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_missing_left_field_has_explicit_finding(case):
    result = _check(case, {key: value for key, value in case["negative"].items()
                           if key != case["comparison"]["left"][1:]})
    assert {finding.rule_id for finding in result.findings} == {case["expected_finding"]}
    assert "missing or invalid" in result.findings[0].explanation


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_wrong_type_does_not_coerce_to_number_or_date(case):
    sample = dict(case["negative"])
    sample[case["comparison"]["left"][1:]] = True
    result = _check(case, sample)
    assert {finding.rule_id for finding in result.findings} == {case["expected_finding"]}


def test_inventory_is_only_submitted_json_not_authoritative_cross_record_assurance():
    assert all(case["scope"] == "submitted_JSON_only" for case in CASES)
    assert "cross-request" in INVENTORY["implementation_boundary"]
