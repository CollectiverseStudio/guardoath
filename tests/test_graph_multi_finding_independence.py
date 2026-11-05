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


def dense_acyclic_graph(edge_count):
    """Construct distinct ordered edges without cycles or external references."""
    nodes = [f"n{i:03d}" for i in range(100)]
    edges = [[left, right] for i, left in enumerate(nodes)
             for right in nodes[i + 1:]][:edge_count]
    assert len(edges) == edge_count
    return {"nodes": nodes, "edges": edges}


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_graph_at_configured_node_and_edge_limits_is_allowed(stage):
    response = assess(dense_acyclic_graph(500), stage)
    assert response.evaluation_status == "complete"
    assert not response.findings
    assert response.recommended_action == "allow"
    assert response.effective_action == "allow"
    assert any(c.checker_id == "graph" and c.status == "passed" for c in response.checker_results)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("document,error_code", [
    ({"nodes": [f"n{i:03d}" for i in range(101)], "edges": []}, "graph_size_limit"),
    (dense_acyclic_graph(501), "graph_size_limit"),
    ({"nodes": ["A", "A"], "edges": [["A", "A"]]}, "graph_duplicate_node"),
])
def test_invalid_graph_limits_or_duplicate_nodes_fail_closed(stage, document, error_code):
    response = assess(document, stage)
    assert response.evaluation_status == "incomplete"
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert not response.findings
    assert any(c.checker_id == "graph" and c.status == "error"
               and c.error_code == error_code for c in response.checker_results)
    assert "n099" not in response.model_dump_json()


@pytest.mark.parametrize("stage,action", [
    (Stage.INPUT, "flag"),
    (Stage.OUTPUT, "block"),
])
@pytest.mark.parametrize("document,expected", [
    ({"nodes": ["A"], "edges": [["A", "A"]]}, {"structure.graph_cycle"}),
    ({"nodes": ["A", "B", "C", "D"], "edges": [["A", "B"], ["A", "C"], ["B", "D"], ["C", "D"]]}, set()),
])
def test_self_loop_and_diamond_dag_are_not_confused(stage, action, document, expected):
    response = assess(document, stage)
    assert response.evaluation_status == "complete"
    assert {finding.rule_id for finding in response.findings} == expected
    assert response.recommended_action == (action if expected else "allow")
    assert response.effective_action == (action if expected else "allow")


def assess_raw(content, stage, *, content_format="json"):
    """Exercise strict JSON and format gating without normalizing the submitted bytes."""
    return asyncio.run(ENGINE.check(
        CheckRequest(
            application_id="demo",
            policy_version="graph-enforce-v1",
            content=content,
            content_format=content_format,
        ),
        stage,
    ))


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("content", [
    '{"nodes":["A"],"nodes":["B"],"edges":[]}',
    '{"nodes":["A"],"edges":[["A","B"]]',
    '{"nodes":["A"],"edges":[["A","A"]],"comment":NaN}',
    '{"nodes":["A"],"edges":[["A",true]]}',
    '{"nodes":["A"],"edges":[["A",null]]}',
])
def test_invalid_graph_documents_fail_closed_without_raw_value_leak(stage, content):
    response = assess_raw(content, stage)
    assert response.evaluation_status == "incomplete"
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert not response.findings
    assert any(c.checker_id == "graph" and c.status == "error"
               and c.error_code == "graph_invalid_document" for c in response.checker_results)
    assert content not in response.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_text_instead_of_json_is_not_a_successful_graph_validation(stage):
    response = assess_raw("nodes: A, B; edges: A -> B", stage, content_format="text")
    assert response.evaluation_status == "incomplete"
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert any(c.checker_id == "graph" and c.status == "skipped"
               and c.error_code == "graph_json_required" for c in response.checker_results)
