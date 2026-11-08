"""Real Engine acceptance: 3+ field graph warnings never replace runtime checks."""
import asyncio
import json

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, FieldComparison, Policy, Rule, Stage


def comparison(rule_id, left, right, operator, kind="number", stage=Stage.INPUT):
    return FieldComparison(rule_id=rule_id, left=left, right=right,
                           operator=operator, value_type=kind, stages=(stage,))


def evaluate(rules, payload, stage):
    policy = Policy(application_id="multi", version="multi-v1", mode="enforce",
                    rules=(Rule(checker_id="cross_field", on_finding=Action.FLAG),),
                    comparisons=tuple(rules))
    request = CheckRequest(application_id="multi", policy_version="multi-v1",
                           content=json.dumps(payload), content_format="json")
    return asyncio.run(Engine([policy], default_checkers()).check(request, stage))


@pytest.mark.parametrize("values,violated", [
    ((1, 2, 3), ["in_ca"]),
    ((2, 2, 2), ["in_ab"]),
    ((3, 2, 4), ["in_ab", "in_ca"]),
])
def test_strict_three_field_numeric_cycle_cannot_all_pass(values, violated):
    rules = (
        comparison("in_ab", "/a", "/b", "lt"),
        comparison("in_bc", "/b", "/c", "le"),
        comparison("in_ca", "/c", "/a", "le"),
    )
    result = evaluate(rules, dict(zip("abc", values)), Stage.INPUT)
    assert result.evaluation_status == "complete"
    assert [f.rule_id for f in result.findings] == ["business." + x for x in violated]
    assert result.recommended_action == Action.FLAG
    assert dict(zip("abc", values)).__repr__() not in repr(result.findings)
    assert evaluate(rules, dict(zip("abc", values)), Stage.OUTPUT).findings == ()


@pytest.mark.parametrize("dates,violated", [
    (("2026-11-08", "2026-11-08", "2026-11-08"), ["out_df"]),
    (("2026-11-08", "2026-11-09", "2026-11-08"), ["out_de", "out_ef", "out_df"]),
])
def test_transitive_equality_conflicts_with_not_equal_across_three_date_fields(dates, violated):
    rules = (
        comparison("out_de", "/d", "/e", "eq", "date", Stage.OUTPUT),
        comparison("out_ef", "/e", "/f", "eq", "date", Stage.OUTPUT),
        comparison("out_df", "/d", "/f", "ne", "date", Stage.OUTPUT),
    )
    result = evaluate(rules, dict(zip("def", dates)), Stage.OUTPUT)
    assert result.evaluation_status == "complete"
    assert [f.rule_id for f in result.findings] == ["business." + x for x in violated]
    assert evaluate(rules, dict(zip("def", dates)), Stage.INPUT).findings == ()


def test_non_strict_three_field_cycle_is_satisfiable_with_equal_numeric_values():
    rules = (
        comparison("ab", "/a", "/b", "le"),
        comparison("bc", "/b", "/c", "le"),
        comparison("ca", "/c", "/a", "le"),
    )
    result = evaluate(rules, {"a": 2, "b": 2, "c": 2}, Stage.INPUT)
    assert result.evaluation_status == "complete"
    assert result.findings == ()
    assert result.recommended_action == Action.ALLOW


def test_indirect_inequality_is_satisfiable_without_forced_equality():
    rules = (
        comparison("de", "/d", "/e", "eq", "date", Stage.OUTPUT),
        comparison("ef", "/e", "/f", "le", "date", Stage.OUTPUT),
        comparison("df", "/d", "/f", "ne", "date", Stage.OUTPUT),
    )
    result = evaluate(rules, {"d": "2026-11-08", "e": "2026-11-08",
                              "f": "2026-11-09"}, Stage.OUTPUT)
    assert result.evaluation_status == "complete"
    assert result.findings == ()


def test_decoded_pointer_boundaries_remain_distinct_in_runtime():
    rules = (
        comparison("escaped", "/a~1b", "/c", "lt"),
        comparison("nested", "/c", "/a/b", "le"),
        comparison("third", "/a/b", "/a~1b", "le"),
    )
    result = evaluate(rules, {"a/b": 3, "a": {"b": 4}, "c": 2}, Stage.INPUT)
    assert result.evaluation_status == "complete"
    assert [f.rule_id for f in result.findings] == ["business.escaped", "business.third"]
