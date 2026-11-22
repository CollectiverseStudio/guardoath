"""P0 contract: every deployable main SHA receives all release gates.

This is a workflow source contract; it does not replace full CI or repeated
real-Engine scans. Documentation and browser-test-only commits need their own
exact-SHA evidence, including the unchanged platform-wide privacy probes.
"""
from pathlib import Path

import pytest


@pytest.mark.parametrize("workflow_name", (
    "field-comparison-evidence.yml",
    "field-reference-acceptance.yml",
    "pii-reliability-gate.yml",
    "stripe-reconciliation-acceptance.yml",
    "deploy-script-acceptance.yml",
))
def test_release_gates_run_for_every_main_commit(workflow_name):
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
                / workflow_name).read_text(encoding="utf-8")
    events = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    # Check the entire event block: paths/paths-ignore or branch exclusions must
    # not silently omit a required gate for a docs-only or browser-only commit.
    event_lines = [line.rstrip() for line in events.splitlines()
                   if line.strip() and not line.lstrip().startswith("#")]
    assert event_lines == [
        "  workflow_dispatch:",
        "  push:",
        "    branches: [main]",
    ], workflow_name


def test_reliability_gate_runs_all_required_real_engine_checks():
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
                / "pii-reliability-gate.yml").read_text(encoding="utf-8")
    for command in (
        "python -m tools.pii_reliability_probe --rounds 20",
        "python -m tools.pii_release_matrix --rounds 20",
        "python -m tools.conversation_privacy_release --rounds 20",
        "python -m tools.conversation_privacy_contention --rounds 10 --concurrency 4",
        "python -m tools.pii_latency_probe --rounds 10 --concurrency 4",
    ):
        assert "run: " + command in workflow, command
    assert "tests/test_privacy_workflow_trigger_contract.py" in workflow
    assert "tests/test_pii_deadline_diagnostics.py" in workflow.split(
        "Verify regex concurrency never changes detector matches", 1
    )[1].split("Repeated real PII detection", 1)[0]
    assert "tests/test_pii_context_window.py" in workflow.split(
        "Verify regex concurrency never changes detector matches", 1
    )[1].split("Repeated real PII detection", 1)[0]
    assert "tests/test_pii_redaction_assembly.py" in workflow.split(
        "Verify regex concurrency never changes detector matches", 1
    )[1].split("Repeated real PII detection", 1)[0]
    assert "windows-latest" in workflow and "ubuntu-latest" in workflow
    assert "fail-fast: false" in workflow


def test_completion_deadline_regression_is_executed_by_privacy_gate():
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
                / "pii-reliability-gate.yml").read_text(encoding="utf-8")
    checks = workflow.split("Verify regex concurrency never changes detector matches", 1)[1]
    assert "tests/test_checker_completion_deadline.py" in checks.split("Repeated real PII detection", 1)[0]
