"""Independent full-engine multi-rule field comparison regression contracts.

This test file intentionally does not inspect or modify the Director-owned
synthetic example generator, tenant record, dashboard, API, or CI settings.
"""
import asyncio
import json

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, FieldComparison, Policy, Rule, Stage


CHAIN = (
    FieldComparison(rule_id="shipped", left="/shipped_qty", right="/ordered_qty", operator="le"),
    FieldComparison(rule_id="delivered", left="/delivered_qty", right="/shipped_qty", operator="le"),
    FieldComparison(rule_id="refund", left="/refund_amount", right="/purchase_amount", operator="le"),
)
BENIGN = {"ordered_qty": 10, "shipped_qty": 8, "delivered_qty": 6,
          "purchase_amount": 100, "refund_amount": 25}


def run(payload, *, comparisons=CHAIN, stage=Stage.INPUT, action="block",
        input_actions=None, output_actions=None, mode="enforce", app="demo"):
    policy = Policy(
        application_id=app, version="multi-rule-v1", mode=mode,
        rules=(Rule(checker_id="cross_field", on_finding=action),),
        comparisons=comparisons,
        input_rule_actions=input_actions,
        output_rule_actions=output_actions,
    )
    request = CheckRequest(application_id=app, policy_version=policy.version,
                           content=json.dumps(payload), content_format="json")
    return asyncio.run(Engine([policy], default_checkers()).check(request, stage))


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_complete_three_rule_portfolio_benign_has_zero_findings(stage):
    result = run(BENIGN, stage=stage)
    assert result.evaluation_status == "complete"
    assert result.findings == ()
    assert result.checker_results[0].status == "passed"
    assert result.recommended_action == Action.ALLOW


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_three_simultaneous_violations_return_all_original_rule_ids(stage):
    bad = {**BENIGN, "shipped_qty": 11, "delivered_qty": 12, "refund_amount": 101}
    result = run(bad, stage=stage)
    assert result.evaluation_status == "complete"
    assert [finding.rule_id for finding in result.findings] == [
        "business.shipped", "business.delivered", "business.refund"
    ]
    assert result.recommended_action == Action.BLOCK


@pytest.mark.parametrize("missing,expected", [
    ("ordered_qty", "business.shipped"),
    ("delivered_qty", "business.delivered"),
    ("purchase_amount", "business.refund"),
])
def test_missing_one_shared_or_independent_field_does_not_hide_other_findings(missing, expected):
    payload = {key: value for key, value in BENIGN.items() if key != missing}
    result = run(payload)
    assert result.evaluation_status == "complete"
    assert expected in {finding.rule_id for finding in result.findings}
    assert any("missing or invalid" in finding.explanation for finding in result.findings)


def test_unrelated_violation_is_not_false_benign_just_because_refund_passes():
    bad = {**BENIGN, "delivered_qty": 9}
    result = run(bad)
    assert [finding.rule_id for finding in result.findings] == ["business.delivered"]
    assert result.recommended_action == Action.BLOCK


def test_input_output_rule_settings_are_independent_for_same_original_finding():
    bad = {**BENIGN, "refund_amount": 101}
    actions = {"business.refund": "off"}
    input_result = run(bad, input_actions=actions, output_actions={"business.refund": "block"})
    output_result = run(bad, stage=Stage.OUTPUT, input_actions=actions,
                        output_actions={"business.refund": "block"})
    assert input_result.findings == ()
    assert input_result.recommended_action == Action.ALLOW
    assert [f.rule_id for f in output_result.findings] == ["business.refund"]
    assert output_result.recommended_action == Action.BLOCK


@pytest.mark.parametrize("own,expected", [
    ("off", Action.ALLOW),
    ("allow", Action.ALLOW),
    ("flag", Action.FLAG),
    ("block", Action.BLOCK),
])
def test_one_rule_override_does_not_change_unrelated_comparison(own, expected):
    bad = {**BENIGN, "refund_amount": 101}
    result = run(bad, input_actions={"business.refund": own})
    assert result.recommended_action == expected
    assert [f.rule_id for f in result.findings] == ([] if own == "off" else ["business.refund"])
    other = run({**BENIGN, "delivered_qty": 9}, input_actions={"business.refund": own})
    assert [f.rule_id for f in other.findings] == ["business.delivered"]
    assert other.recommended_action == Action.BLOCK


def test_stage_scoping_executes_only_the_comparisons_for_that_stage():
    rules = (
        FieldComparison(rule_id="input_limit", left="/a", right="/b",
                        operator="le", stages=(Stage.INPUT,)),
        FieldComparison(rule_id="output_limit", left="/c", right="/d",
                        operator="le", stages=(Stage.OUTPUT,)),
    )
    values = {"a": 9, "b": 1, "c": 8, "d": 2}
    assert [f.rule_id for f in run(values, comparisons=rules).findings] == ["business.input_limit"]
    assert [f.rule_id for f in run(values, comparisons=rules, stage=Stage.OUTPUT).findings] == [
        "business.output_limit"
    ]


def test_contradictory_strict_comparisons_both_report_on_equal_input():
    rules = (
        FieldComparison(rule_id="a_b", left="/a", right="/b", operator="lt"),
        FieldComparison(rule_id="b_a", left="/b", right="/a", operator="lt"),
    )
    result = run({"a": 1, "b": 1}, comparisons=rules)
    assert {f.rule_id for f in result.findings} == {"business.a_b", "business.b_a"}
    assert result.recommended_action == Action.BLOCK


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("missing,expected", [
    ("shipped_qty", {"business.shipped", "business.delivered"}),
    ("refund_amount", {"business.refund"}),
    ("ordered_qty", {"business.shipped"}),
    ("purchase_amount", {"business.refund"}),
])
def test_missing_field_reports_every_affected_rule_without_suppressing_others(stage, missing, expected):
    payload = {key: value for key, value in BENIGN.items() if key != missing}
    result = run(payload, stage=stage)
    assert {finding.rule_id for finding in result.findings} == expected
    assert result.evaluation_status == "complete"


@pytest.mark.parametrize("invalid", [None, True, "8", [], {}])
def test_invalid_shared_left_does_not_mask_both_dependent_rules(invalid):
    payload = {**BENIGN, "shipped_qty": invalid}
    result = run(payload)
    assert {finding.rule_id for finding in result.findings} == {
        "business.shipped", "business.delivered"
    }
    assert result.recommended_action == Action.BLOCK


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_shared_engine_tenant_isolation_during_concurrent_requests(stage):
    policies = (
        Policy(application_id="alpha", version="multi-rule-v1", mode="enforce",
               rules=(Rule(checker_id="cross_field", on_finding="flag"),),
               comparisons=CHAIN,
               input_rule_actions={"business.refund": "block"},
               output_rule_actions={"business.refund": "block"}),
        Policy(application_id="beta", version="multi-rule-v1", mode="enforce",
               rules=(Rule(checker_id="cross_field", on_finding="flag"),),
               comparisons=CHAIN,
               input_rule_actions={"business.refund": "off"},
               output_rule_actions={"business.refund": "off"}),
    )
    engine = Engine(policies, default_checkers())
    content = json.dumps({**BENIGN, "refund_amount": 101})

    async def parallel():
        requests = [
            engine.check(CheckRequest(application_id=owner, policy_version="multi-rule-v1",
                                      content=content, content_format="json"), stage)
            for owner in ("alpha", "beta") for _ in range(4)
        ]
        return await asyncio.gather(*requests)

    results = asyncio.run(parallel())
    first, second = results[:4], results[4:]
    assert all({finding.rule_id for finding in result.findings} == {"business.refund"}
               and result.recommended_action == Action.BLOCK for result in first)
    assert all(result.findings == () and result.recommended_action == Action.ALLOW
               for result in second)
