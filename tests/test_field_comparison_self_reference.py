"""Real Engine semantics behind dashboard's self-pointer logic diagnostics.

Valid comparisons of a value to itself are deterministic; missing values are
still findings. Stage action and JSON Pointer escaping remain authoritative.
"""
import asyncio
import json

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, FieldComparison, Policy, Rule, Stage


def run(comparison, payload, stage):
    policy = Policy(application_id="self-test", version="self-v1", mode="enforce",
                    rules=(Rule(checker_id="cross_field", on_finding=Action.FLAG),),
                    comparisons=(comparison,))
    request = CheckRequest(application_id="self-test", policy_version="self-v1",
                           content=json.dumps(payload), content_format="json")
    return asyncio.run(Engine([policy], default_checkers()).check(request, stage))


@pytest.mark.parametrize("operator,expected", [
    ("lt", True), ("gt", True), ("ne", True),
    ("le", False), ("ge", False), ("eq", False),
])
@pytest.mark.parametrize("kind,value", [
    ("number", 4), ("date", "2026-11-08"),
])
@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_same_valid_field_is_impossible_or_non_discriminating_with_real_engine(operator, expected, kind, value, stage):
    comparison = FieldComparison(rule_id="self_rule", left="/field", right="/field",
                                 operator=operator, value_type=kind, stages=(stage,))
    result = run(comparison, {"field": value}, stage)
    assert result.evaluation_status == "complete"
    assert [f.rule_id for f in result.findings] == (["business.self_rule"] if expected else [])
    assert result.recommended_action == (Action.FLAG if expected else Action.ALLOW)
    assert str(value) not in str(result.findings)
    opposite = Stage.OUTPUT if stage == Stage.INPUT else Stage.INPUT
    assert run(comparison, {"field": value}, opposite).findings == ()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_self_equality_missing_field_still_finds_invalid_input(stage):
    comparison = FieldComparison(rule_id="self_rule", left="/field", right="/field",
                                 operator="eq", stages=(stage,))
    result = run(comparison, {"other": 4}, stage)
    assert result.evaluation_status == "complete"
    assert [f.rule_id for f in result.findings] == ["business.self_rule"]
    assert result.recommended_action == Action.FLAG


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_encoded_literal_slash_and_nested_path_do_not_alias(stage):
    comparison = FieldComparison(rule_id="escaped", left="/a~1b", right="/a/b",
                                 operator="le", stages=(stage,))
    result = run(comparison, {"a/b": 9, "a": {"b": 2}}, stage)
    assert result.evaluation_status == "complete"
    assert [f.rule_id for f in result.findings] == ["business.escaped"]
    assert result.recommended_action == Action.FLAG
