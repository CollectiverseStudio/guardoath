"""P0 contract: shipped privacy gate follows ALL runtime checker and policy edits.

This is a workflow source contract; it does not replace full CI or repeated
real-Engine scans. Keep the privacy gate narrowly path-scoped to avoid unrelated
changes running expensive platform-wide PII probes.
"""
from pathlib import Path


def test_reliability_gate_cannot_omit_shipped_privacy_paths():
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
                / "pii-reliability-gate.yml").read_text(encoding="utf-8")
    # Exact indented YAML path lines, never a prose mention.
    required_paths = (
        "guardoath/pii.py",
        "guardoath/engine.py",
        "guardoath/checkers.py",
        "guardoath/secrets_check.py",
        "guardoath/encoded_pii.py",
        "guardoath/text_security.py",
        "guardoath/privacy_catalog.py",
        "guardoath/sensitive_fields.py",
        "guardoath/privacy_patterns/**",
        "policies/playground.json",
        "tests/test_dashboard.py",
        "tests/test_pii_deadline_diagnostics.py",
        "tests/test_checker_completion_deadline.py",
        "tests/test_pii_context_window.py",
        "tests/test_pii_redaction_assembly.py",
        "tests/test_pii_core_validation_cache.py",
        "tools/conversation_privacy_release.py",
        "tools/conversation_privacy_contention.py",
        "tests/test_conversation_privacy_release.py",
        "tests/test_conversation_privacy_contention.py",
        "tests/test_privacy_workflow_trigger_contract.py",
        ".github/workflows/pii-reliability-gate.yml",
    )
    for path in required_paths:
        assert '      - "' + path + '"' in workflow, path
    assert "branches: [main]" in workflow
    assert "workflow_dispatch:" in workflow


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
