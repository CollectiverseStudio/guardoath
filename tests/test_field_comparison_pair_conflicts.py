"""Real shared Engine witnesses for pairwise operator conflict dashboard warnings.

Diagnostics do not replace tenant policy or become a second validation engine.
"""
import asyncio
import json

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, FieldComparison, Policy, Rule, Stage


def run(comparisons, payload, stage):
    policy = Policy(
        application_id="pair-test", version="pair-v1", mode="enforce",
        rules=(Rule(checker_id="cross_field", on_finding=Action.FLAG),),
        comparisons=tuple(comparisons),
    )
    request = CheckRequest(
        application_id="pair-test", policy_version="pair-v1",
        content=json.dumps(payload), content_format="json",
    )
    return asyncio.run(Engine([policy], default_checkers()).check(request, stage))


def comp(rule_id, left, right, operator, kind="number", stages=(Stage.INPUT,)):
    return FieldComparison(
        rule_id=rule_id, left=left, right=right,
        operator=operator, value_type=kind, stages=stages,
    )


@pytest.mark.parametrize("a,b,violating", [
    (1, 3, "reverse"), (2, 2, "less"), (4, 2, "less"),
])
def test_opposite_strict_and_inclusive_relations_cannot_both_pass(a, b, violating):
    rules = (comp("less", "/a", "/b", "lt"), comp("reverse", "/b", "/a", "le"))
    result = run(rules, {"a": a, "b": b}, Stage.INPUT)
    assert result.evaluation_status == "complete"
    assert [f.rule_id for f in result.findings] == ["business." + violating]
    assert result.recommended_action == Action.FLAG
    assert str({"a": a, "b": b}) not in repr(result.findings)
    assert run(rules, {"a": a, "b": b}, Stage.OUTPUT).findings == ()


@pytest.mark.parametrize("first,second,violating", [
    ("2026-11-08", "2026-11-08", "different"),
    ("2026-11-08", "2026-11-09", "equal"),
])
def test_equality_and_inequality_cannot_both_pass_with_calendar_dates(first, second, violating):
    rules = (
        comp("equal", "/stamp", "/other", "eq", "date", (Stage.OUTPUT,)),
        comp("different", "/other", "/stamp", "ne", "date", (Stage.OUTPUT,)),
    )
    result = run(rules, {"stamp": first, "other": second}, Stage.OUTPUT)
    assert result.evaluation_status == "complete"
    assert [f.rule_id for f in result.findings] == ["business." + violating]
    assert run(rules, {"stamp": first, "other": second}, Stage.INPUT).findings == ()


def test_reversed_consistent_constraints_and_json_pointer_slash_are_benign():
    rules = (
        comp("less", "/a", "/b", "lt"),
        comp("greater", "/b", "/a", "gt"),
        comp("slash", "/slash~1field", "/nest/value", "le"),
    )
    result = run(rules, {"a": 1, "b": 2, "slash/field": 1,
                         "nest": {"value": 2}}, Stage.INPUT)
    assert result.evaluation_status == "complete"
    assert result.findings == ()
    assert result.recommended_action == Action.ALLOW


def test_all_findings_are_reported_when_two_independent_pairs_violate():
    rules = (
        comp("less", "/a", "/b", "lt"),
        comp("reverse", "/b", "/a", "le"),
        comp("equal", "/x", "/y", "eq"),
        comp("different", "/x", "/y", "ne"),
    )
    result = run(rules, {"a": 5, "b": 2, "x": 10, "y": 10}, Stage.INPUT)
    assert result.evaluation_status == "complete"
    assert {f.rule_id for f in result.findings} == {"business.less", "business.different"}
    assert result.recommended_action == Action.FLAG
