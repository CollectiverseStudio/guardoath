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


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_nested_pointer_chain_reports_all_affected_original_rules(stage):
    rules = (
        FieldComparison(rule_id="ordered", left="/orders/shipped", right="/orders/requested", operator="le"),
        FieldComparison(rule_id="delivered", left="/orders/delivered", right="/orders/shipped", operator="le"),
    )
    benign = {"orders": {"requested": 10, "shipped": 8, "delivered": 6}}
    assert run(benign, comparisons=rules, stage=stage).findings == ()
    wrong = {"orders": {"requested": 7, "shipped": 8, "delivered": 9}}
    result = run(wrong, comparisons=rules, stage=stage)
    assert [finding.rule_id for finding in result.findings] == [
        "business.ordered", "business.delivered"
    ]


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_json_pointer_escaping_with_multi_rule_overlap(stage):
    rules = (
        FieldComparison(rule_id="slash", left="/a~1b", right="/limit", operator="le"),
        FieldComparison(rule_id="tilde", left="/c~0d", right="/a~1b", operator="le"),
    )
    benign = {"a/b": 5, "c~d": 3, "limit": 10}
    assert run(benign, comparisons=rules, stage=stage).findings == ()
    wrong = {"a/b": 12, "c~d": 13, "limit": 10}
    result = run(wrong, comparisons=rules, stage=stage)
    assert {finding.rule_id for finding in result.findings} == {"business.slash", "business.tilde"}


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_shared_number_and_date_pointer_is_invalid_for_only_one_type(stage):
    rules = (
        FieldComparison(rule_id="number", left="/shared", right="/num_limit",
                        operator="le", value_type="number"),
        FieldComparison(rule_id="date", left="/shared", right="/date_limit",
                        operator="le", value_type="date"),
    )
    numeric = {"shared": 3, "num_limit": 4, "date_limit": "2026-11-30"}
    date_value = {"shared": "2026-11-03", "num_limit": 4, "date_limit": "2026-11-30"}
    for payload, expected in ((numeric, "business.date"), (date_value, "business.number")):
        result = run(payload, comparisons=rules, stage=stage)
        assert {finding.rule_id for finding in result.findings} == {expected}
        assert result.evaluation_status == "complete"
        assert result.recommended_action == Action.BLOCK


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_equal_opposite_pointer_constraints_preserve_both_findings(stage):
    rules = (
        FieldComparison(rule_id="same", left="/a", right="/b", operator="eq"),
        FieldComparison(rule_id="different", left="/a", right="/b", operator="ne"),
    )
    for payload in ({"a": 1, "b": 1}, {"a": 1, "b": 2}):
        result = run(payload, comparisons=rules, stage=stage)
        assert len(result.findings) == 1
        assert {finding.rule_id for finding in result.findings} == ({
            "business.different"} if payload["a"] == payload["b"] else {"business.same"})
        assert result.recommended_action == Action.BLOCK


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("operator,accepted,rejected", [
    ("lt", 9, 10),
    ("le", 10, 11),
    ("eq", 10, 9),
    ("ge", 10, 9),
    ("gt", 11, 10),
    ("ne", 11, 10),
])
def test_all_operators_respect_exact_boundary_in_a_two_rule_portfolio(stage, operator, accepted, rejected):
    """The selected rule must not cause a benign companion rule to fail."""
    rules = (
        FieldComparison(rule_id="boundary", left="/value", right="/threshold", operator=operator),
        FieldComparison(rule_id="companion", left="/quantity", right="/quantity_limit", operator="le"),
    )
    payload = {"value": accepted, "threshold": 10, "quantity": 3, "quantity_limit": 10}
    clean = run(payload, comparisons=rules, stage=stage)
    assert clean.evaluation_status == "complete"
    assert clean.findings == () and clean.recommended_action == Action.ALLOW

    violated = run({**payload, "value": rejected}, comparisons=rules, stage=stage)
    assert violated.evaluation_status == "complete"
    assert [finding.rule_id for finding in violated.findings] == ["business.boundary"]
    assert violated.recommended_action == Action.BLOCK


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_invalid_iso_date_and_numeric_failure_report_both_original_rules(stage):
    rules = (
        FieldComparison(rule_id="calendar", left="/start", right="/end", operator="le", value_type="date"),
        FieldComparison(rule_id="budget", left="/spent", right="/budget", operator="le"),
    )
    payload = {"start": "2026-02-30", "end": "2026-03-01", "spent": 12, "budget": 10}
    result = run(payload, comparisons=rules, stage=stage)
    assert [finding.rule_id for finding in result.findings] == [
        "business.calendar", "business.budget"
    ]
    assert result.evaluation_status == "complete"
    assert result.recommended_action == Action.BLOCK


def test_decimal_precision_is_not_lost_when_rules_share_a_pointer():
    rules = (
        FieldComparison(rule_id="not_above", left="/a", right="/b", operator="le"),
        FieldComparison(rule_id="above", left="/a", right="/b", operator="gt"),
    )
    policy = Policy(application_id="precision", version="multi-rule-v1", mode="enforce",
                    rules=(Rule(checker_id="cross_field", on_finding="block"),), comparisons=rules)
    request = CheckRequest(
        application_id="precision", policy_version=policy.version,
        content='{"a":0.10000000000000000001,"b":0.1}', content_format="json",
    )
    result = asyncio.run(Engine([policy], default_checkers()).check(request, Stage.INPUT))
    assert result.evaluation_status == "complete"
    assert [finding.rule_id for finding in result.findings] == ["business.not_above"]
    assert result.recommended_action == Action.BLOCK


@pytest.mark.parametrize("raw_json", [
    '{"shipped_qty":8,"shipped_qty":9,"ordered_qty":10}',
    '{"shipped_qty":',
    '{"shipped_qty":NaN,"ordered_qty":10}',
])
def test_invalid_json_reports_one_sanitized_document_finding_not_partial_comparison_findings(raw_json):
    policy = Policy(
        application_id="malformed", version="multi-rule-v1", mode="enforce",
        rules=(Rule(checker_id="cross_field", on_finding="block"),), comparisons=CHAIN,
    )
    request = CheckRequest(application_id="malformed", policy_version=policy.version,
                           content=raw_json, content_format="json")
    result = asyncio.run(Engine([policy], default_checkers()).check(request, Stage.INPUT))
    assert result.evaluation_status == "complete"
    assert [finding.rule_id for finding in result.findings] == ["business.invalid_json"]
    assert result.recommended_action == Action.BLOCK
    assert all(raw_json not in finding.explanation for finding in result.findings)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_invalid_secret_like_field_content_is_not_echoed_in_findings(stage):
    sentinel = "TEST_SECRET_VALUE_DO_NOT_ECHO_8b1ca42"
    payload = {**BENIGN, "shipped_qty": sentinel}
    result = run(payload, stage=stage)
    assert {finding.rule_id for finding in result.findings} == {
        "business.shipped", "business.delivered"
    }
    assert all(sentinel not in finding.explanation for finding in result.findings)
