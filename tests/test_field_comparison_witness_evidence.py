"""Witness/classification contracts, not generator acceptance."""
import asyncio
import json

import pytest

from guardoath.models import FieldComparison
from tools import field_comparison_witnesses as audit


@pytest.fixture(scope="module")
def report():
    return asyncio.run(audit.run_audit())


@pytest.mark.parametrize("witness", ["W1-shared-pointer", "W2-overwrite",
                                     "W3-feasible-dag", "W5-escaped-pointer"])
@pytest.mark.parametrize("stage", ["input", "output"])
def test_explicit_feasible_manual_witness_is_complete_allow_for_entire_portfolio(report, witness, stage):
    row = next(w for w in report["witnesses"] if w["witness_id"] == witness)
    case = next(c for c in row["manual_benign"] if c["stage"] == stage)
    assert case["accepted"] is True and case["finding_ids"] == []
    assert case["evaluation_status"] == "complete"
    assert case["recommended_action"] == case["effective_action"] == "allow"
    assert case["checker_results"][0]["status"] == "passed"


@pytest.mark.parametrize("witness", ["W1-shared-pointer", "W2-overwrite", "W3-feasible-dag",
                                     "W4-contradiction", "W5-escaped-pointer"])
@pytest.mark.parametrize("stage", ["input", "output"])
def test_all_simultaneous_violations_keep_every_original_finding_and_block(report, witness, stage):
    row = next(w for w in report["witnesses"] if w["witness_id"] == witness)
    case = next(c for c in row["manual_all_violations"] if c["stage"] == stage)
    assert case["accepted"] is True and case["all_original_findings_preserved"] is True
    expected = {
        "W1-shared-pointer": ["business.shipped", "business.delivered", "business.refund"],
        "W2-overwrite": ["business.above_minimum", "business.below_maximum"],
        "W3-feasible-dag": ["business.requested", "business.stock"],
        "W4-contradiction": ["business.forward", "business.reverse"],
        "W5-escaped-pointer": ["business.escaped", "business.ceiling"],
    }
    assert case["finding_ids"] == expected[witness]
    assert len(set(case["finding_ids"])) == len(case["finding_ids"])
    assert case["evaluation_status"] == "complete"
    assert case["recommended_action"] == case["effective_action"] == "block"


def test_strict_cycle_is_not_assigned_a_fabricated_benign_witness(report):
    row = next(w for w in report["witnesses"] if w["witness_id"] == "W4-contradiction")
    assert row["classification"] == "infeasible_proven"
    assert row["manual_benign"] == []
    assert row["infeasibility_proof"] == "strict_numeric_two_cycle_no_joint_witness"
    assert not any(c["accepted"] for c in row["generated_cases"] if c["kind"] == "benign")


def test_infeasibility_proof_refuses_nonmatching_constraints():
    rules = (FieldComparison(rule_id="a", left="/a", right="/b", operator="le"),
             FieldComparison(rule_id="b", left="/b", right="/a", operator="le"))
    with pytest.raises(ValueError, match="Not the stated"):
        audit.strict_cycle_proof(rules)


def test_reports_have_no_generated_or_manual_document_values(report):
    serialized = json.dumps(report)
    assert '"manual_bad"' not in serialized
    assert '"comparisons"' not in serialized
    assert '"content"' not in serialized
    for name in ("shipped_qty", "ordered_qty", "refund_amount", "a/b", "c~d"):
        assert name not in serialized
