"""Independent proof for impossible Field Comparison portfolios, never fake clean samples."""
import asyncio

import pytest

from guardoath.models import FieldComparison
from tools.field_comparison_portfolio import (
    _constraint_feasibility, _infeasibility_proved, run_audit,
)


def rule(name, left, right, op, *, value_type="number"):
    return FieldComparison(rule_id=name, left=left, right=right,
                           operator=op, value_type=value_type)


def test_strict_two_cycle_has_no_joint_benign_document():
    comparisons = (rule("first", "/a", "/b", "lt"),
                   rule("second", "/b", "/a", "lt"))
    for target in comparisons:
        assert _infeasibility_proved(
            comparisons, "business." + target.rule_id, "benign",
            {"code": "cannot_all_pass"})


def test_self_comparison_proves_both_distinct_impossibilities():
    comparisons = (rule("self_le", "/a", "/a", "le"),
                   rule("self_lt", "/a", "/a", "lt"))
    assert _infeasibility_proved(
        comparisons, "business.self_le", "positive",
        {"code": "cannot_fail_alone"})
    assert _infeasibility_proved(
        comparisons, "business.self_lt", "benign",
        {"code": "cannot_all_pass"})
    assert not _infeasibility_proved(
        comparisons, "business.self_lt", "positive",
        {"code": "cannot_fail_alone"})


@pytest.mark.parametrize("kind,reason", [
    ("positive", None),
    ("benign", None),
    ("positive", {"code": "search_limit"}),
    ("benign", {"code": "listing_limit"}),
    ("positive", {"code": "cannot_all_pass"}),
    ("benign", {"code": "cannot_fail_alone"}),
])
def test_missing_witness_or_wrong_reason_never_passes(kind, reason):
    comparisons = (rule("safe", "/a", "/b", "le"),)
    assert not _infeasibility_proved(
        comparisons, "business.safe", kind, reason)


def test_known_feasible_chain_and_unknown_type_conflicts_are_not_proofs():
    valid = (rule("one", "/a", "/b", "lt"),
             rule("two", "/b", "/c", "lt"))
    assert _constraint_feasibility([(c.left, c.right, c.operator) for c in valid]) is True
    assert not _infeasibility_proved(valid, "business.one", "benign",
                                    {"code": "cannot_all_pass"})
    mixed = (rule("number", "/a", "/b", "lt"),
             rule("date", "/a", "/c", "ge", value_type="date"))
    assert not _infeasibility_proved(mixed, "business.number", "benign",
                                    {"code": "cannot_all_pass"})


def test_not_equal_branches_must_both_fail_before_proving_impossible():
    assert _constraint_feasibility([("/a", "/b", "eq"),
                                    ("/a", "/b", "ne")]) is False
    assert _constraint_feasibility([("/a", "/b", "ne")]) is True
    assert _constraint_feasibility([("/a", "/b", "le")]) is True


def test_bounded_proof_never_turns_unknown_into_impossibility():
    # Conservative resource limit; the engine and generator may still evaluate.
    relations = [(f"/left_{i}", f"/right_{i}", "lt") for i in range(257)]
    assert _constraint_feasibility(relations) is None


def test_exact_current_portfolio_real_engine_both_stages_accepts_only_proven_unavailable():
    report = asyncio.run(run_audit())
    cases = [case for scenario in report["scenarios"] for case in scenario["cases"]]
    assert len(cases) == 168
    assert report["acceptance_passed"], [c for c in cases if not c["accepted"]]
    impossible = [c for c in cases if c["status"] == "unavailable"]
    assert len(impossible) == 10
    assert all(c["accepted"] and c["proof_status"] == "independently_proved_impossible"
               for c in impossible)
    assert all(c["accepted"] and c["evaluation_status"] == "complete"
               for c in cases if c["status"] == "evaluated")
    assert report["release_readiness"] is False
