"""Independent regression evidence for Graph structure multi-finding behavior.

These checks use synthetic local JSON and an existing enforce-mode graph policy.
They do not assert graph provenance, business authorization, or cycle safety.
"""
import asyncio
import json
from pathlib import Path

import pytest

from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Stage


ROOT = Path(__file__).resolve().parents[1]
POLICIES = load_policies(ROOT / "policies" / "graph-batch.json")
ENGINE = Engine(POLICIES, default_checkers())


def assess(document, stage):
    return asyncio.run(ENGINE.check(
        CheckRequest(
            application_id="demo",
            policy_version="graph-enforce-v1",
            content=json.dumps(document, separators=(",", ":")),
            content_format="json",
        ),
        stage,
    ))


@pytest.mark.parametrize("stage,expected_action", [
    (Stage.INPUT, "flag"),
    (Stage.OUTPUT, "block"),
])
@pytest.mark.parametrize("document,expected_rules", [
    (
        {"nodes": ["A", "B"], "edges": [["A", "C"], ["A", "C"]]},
        {"structure.graph_duplicate_edge", "structure.graph_unknown_node"},
    ),
    (
        {"nodes": ["A", "B"], "edges": [["A", "B"], ["B", "A"], ["B", "A"]]},
        {"structure.graph_duplicate_edge", "structure.graph_cycle"},
    ),
])
def test_all_independent_graph_violations_survive_engine(stage, expected_action, document, expected_rules):
    response = assess(document, stage)
    assert response.mode == "enforce"
    assert response.evaluation_status == "complete"
    assert {finding.rule_id for finding in response.findings} == expected_rules
    assert len(response.findings) == len(expected_rules)
    assert response.recommended_action == expected_action
    assert response.effective_action == expected_action
    assert any(c.checker_id == "graph" and c.status == "flagged" for c in response.checker_results)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_well_formed_acyclic_unique_graph_is_benign(stage):
    response = assess({"nodes": ["A", "B", "C"], "edges": [["A", "B"], ["B", "C"]]}, stage)
    assert response.evaluation_status == "complete"
    assert not response.findings
    assert response.recommended_action == "allow"
    assert response.effective_action == "allow"


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_graph_findings_do_not_echo_submitted_identifiers(stage):
    secret_like = "PRIVATE_IDENTIFIER_123456"
    response = assess({
        "nodes": ["A", secret_like],
        "edges": [["A", "MISSING"], ["A", "MISSING"]],
    }, stage)
    assert {finding.rule_id for finding in response.findings} == {
        "structure.graph_duplicate_edge",
        "structure.graph_unknown_node",
    }
    assert secret_like not in response.model_dump_json()
    assert "MISSING" not in response.model_dump_json()
