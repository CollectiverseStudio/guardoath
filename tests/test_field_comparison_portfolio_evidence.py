"""Strict audit contracts; passing these tests does not mean generator acceptance."""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, FieldComparison, Policy, Rule, Stage
from tools import field_comparison_portfolio as audit


def engine():
    comparisons = (
        FieldComparison(rule_id="first", left="/a", right="/b", operator="le"),
        FieldComparison(rule_id="second", left="/b", right="/c", operator="le"),
    )
    policy = Policy(application_id="audit", version="v1", mode="enforce",
                    rules=(Rule(checker_id="cross_field", on_finding="block"),),
                    comparisons=comparisons)
    return Engine([policy], default_checkers()), policy


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_target_only_nonmatch_cannot_hide_other_active_findings(stage):
    eng, policy = engine()
    row = asyncio.run(audit.audit_example(eng, policy, "business.first",
                      '{"a":1,"b":2,"c":1}', "benign", stage))
    assert row["accepted"] is False
    assert row["finding_ids"] == ["business.second"]
    assert row["evaluation_status"] == "complete"
    assert row["recommended_action"] == row["effective_action"] == "block"


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_complete_joint_witness_is_allow_and_positive_retains_overlap(stage):
    eng, policy = engine()
    clean = asyncio.run(audit.audit_example(eng, policy, "business.first",
                        '{"a":1,"b":2,"c":3}', "benign", stage))
    assert clean["accepted"] is True and clean["finding_ids"] == []
    bad = asyncio.run(audit.audit_example(eng, policy, "business.first",
                      '{"a":3,"b":2,"c":1}', "positive", stage))
    assert bad["accepted"] is True
    assert bad["finding_ids"] == ["business.first", "business.second"]


def test_unavailable_without_generator_reason_is_not_invented_acceptance():
    eng, policy = engine()
    row = asyncio.run(audit.audit_example(eng, policy, "business.first", None,
                                        "benign", Stage.INPUT))
    assert row["accepted"] is False and row["status"] == "unavailable"
    assert row["reason"] == "generator_returned_none_without_reason"


def test_report_never_echoes_generated_document_or_submitted_values():
    eng, policy = engine()
    marker = "private-synthetic-marker"
    row = asyncio.run(audit.audit_example(eng, policy, "business.first",
                      json.dumps({"a": marker, "b": 2, "c": 3}), "benign", Stage.INPUT))
    assert row["accepted"] is False
    assert marker not in json.dumps(row)


def test_incomplete_checker_cannot_pass_benign_acceptance():
    class Incomplete:
        async def check(self, request, stage):
            return SimpleNamespace(findings=(), checker_results=(SimpleNamespace(
                checker_id="cross_field", status="skipped", error_code="checker_timeout"),),
                evaluation_status="incomplete", recommended_action=Action.ALLOW,
                effective_action=Action.ALLOW)
    _, policy = engine()
    row = asyncio.run(audit.audit_example(Incomplete(), policy, "business.first", "{}",
                                        "benign", Stage.INPUT))
    assert row["accepted"] is False and row["evaluation_status"] == "incomplete"


def test_strict_command_writes_failed_evidence_and_returns_nonzero(monkeypatch, tmp_path):
    async def failed():
        return {"acceptance_passed": False, "scenarios": []}
    monkeypatch.setattr(audit, "run_audit", failed)
    path = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["audit", "--output", str(path)])
    assert audit.main() == 1
    assert json.loads(path.read_text())["acceptance_passed"] is False
