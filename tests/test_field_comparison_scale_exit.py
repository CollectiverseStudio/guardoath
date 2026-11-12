"""Field Comparisons scale exit status must reflect real schema and worker failures.

All inputs here are synthetic measurement metadata. No external providers/AWS.
"""
import json
from pathlib import Path
import sys

import pytest

from tools import field_comparison_scale as scale


def measured(size, component="checker", *, schema_ok=True):
    return {
        "component": component,
        "size": size,
        "status": "measured",
        "samples": 1,
        "median_ms": 1.0,
        "p95_nearest_rank_ms": 1.0,
        "peak_traced_bytes": 1024,
        "output_bytes": 100,
        "schema_acceptance": {
            "policy": {"accepted": schema_ok, "errors": [] if schema_ok else [{"path": ["comparisons"], "type": "too_long"}]},
            "tenant_record": {"accepted": True, "errors": []},
        },
    }


def test_all_requested_components_and_sizes_are_required():
    expected = [measured(n, kind) for n in (30, 51) for kind in ("checker", "generator")]
    assert scale.scale_acceptance(expected, sizes=(30, 51))
    assert not scale.scale_acceptance(expected[:-1], sizes=(30, 51))
    assert not scale.scale_acceptance(expected + [expected[0]], sizes=(30, 51))
    assert not scale.scale_acceptance(expected + [measured(500)], sizes=(30, 51))


@pytest.mark.parametrize("bad", [
    {"component": "checker", "size": 30, "status": "timeout", "timeout_seconds": 10},
    {"component": "checker", "size": 30, "status": "worker_error", "returncode": 1},
    measured(30, schema_ok=False),
    {**measured(30), "samples": 0},
    {**measured(30), "median_ms": float("nan")},
    {**measured(30), "schema_acceptance": {"policy": {"accepted": True, "errors": []}}},
])
def test_missing_schema_timeout_or_bad_measurement_never_passes(bad):
    assert not scale.scale_acceptance([bad], sizes=(30,))


def test_cli_emits_report_but_exits_nonzero_on_scale_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(scale, "SIZES", (30, 51))
    monkeypatch.setattr(scale, "bounded_measure",
                        lambda kind, n, samples, timeout: (
                            {"component": kind, "size": n, "status": "timeout", "timeout_seconds": timeout}
                            if (n, kind) == (51, "generator") else measured(n, kind)))
    output = tmp_path / "scale.json"
    monkeypatch.setattr(sys, "argv", ["field_comparison_scale", "--samples", "1",
                                     "--timeout", "1", "--output", str(output)])
    assert scale.main() == 1
    report = json.loads(output.read_text())
    assert report["scale_acceptance_passed"] is False
    assert report["release_readiness"] is False
    assert len(report["measurements"]) == 4
    assert any(r["status"] == "timeout" for r in report["measurements"])


def test_cli_succeeds_only_when_every_component_and_schema_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(scale, "SIZES", (30, 51))
    monkeypatch.setattr(scale, "bounded_measure",
                        lambda kind, n, samples, timeout: measured(n, kind))
    output = tmp_path / "scale.json"
    monkeypatch.setattr(sys, "argv", ["field_comparison_scale", "--samples", "1", "--output", str(output)])
    assert scale.main() == 0
    report = json.loads(output.read_text())
    assert report["scale_acceptance_passed"] is True
    assert report["release_readiness"] is False


def test_single_component_worker_cli_succeeds_only_with_real_measurement(tmp_path, monkeypatch):
    monkeypatch.setattr(scale, "measure", lambda component, size, samples: measured(size, component))
    output = tmp_path / "worker.json"
    monkeypatch.setattr(sys, "argv", ["field_comparison_scale", "--worker", "checker",
                                     "--size", "51", "--samples", "1", "--output", str(output)])
    assert scale.main() == 0
    assert json.loads(output.read_text())["component"] == "checker"
    monkeypatch.setattr(scale, "measure", lambda component, size, samples: measured(size, component, schema_ok=False))
    assert scale.main() == 1


def test_field_comparison_evidence_requires_real_customer_browser_gate():
    """Strict portfolio green must not substitute for authenticated UI acceptance."""
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" /
                "field-comparison-evidence.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert 'branches: [main]' in workflow
    assert '      - "guardoath/static/comparison_editor.js"' in workflow
    for path in (
        "guardoath/static/arithmetic_editor.js",
        "guardoath/static/identity_editor.js",
        "guardoath/static/conversion_editor.js",
        "tests/browser/*editor.spec.js",
        "tests/browser/arithmetic_500.spec.js",
        "tests/browser/protected_reference_editors.spec.js",
        "tests/browser/field_comparison_mixed_family_scale.spec.js",
        "tests/browser/field_comparisons_500.spec.js",
    ):
        assert '      - "' + path + '"' in workflow, path
    assert "strict-portfolio-and-scale:" in workflow
    browser = workflow.split("  customer-browser-acceptance:", 1)
    assert len(browser) == 2
    job = browser[1]
    assert "runs-on: ubuntu-latest" in job
    assert 'PLAYWRIGHT_CHANNEL: chrome' in job
    assert "run: npm ci" in job
    assert "npx playwright test" in job
    for family in (
        "tests/browser/field_comparison",
        "tests/browser/field_comparisons_500",
        "tests/browser/fc_comparison",
        "tests/browser/arithmetic",
        "tests/browser/identity",
        "tests/browser/conversion",
        "tests/browser/trusted_reference",
        "tests/browser/protected_reference_editors",
    ):
        assert family in job, family


def test_field_comparison_browser_evidence_is_durable_and_fail_closed():
    """An all-green headline is not proof of which Chrome cases actually ran."""
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" /
                "field-comparison-evidence.yml").read_text(encoding="utf-8")
    browser = workflow.split("  customer-browser-acceptance:", 1)
    assert len(browser) == 2
    job = browser[1]
    # Real Playwright writes a machine-readable case-level report, not an
    # invented count derived from pytest or a successful npm installation.
    assert "PLAYWRIGHT_JUNIT_OUTPUT_FILE: artifacts/field-comparisons/browser-junit.xml" in job
    assert "npx playwright test --reporter=line,junit" in job
    assert 'printf \'%s\\n\' "$GITHUB_SHA" > artifacts/field-comparisons/browser-source-commit.txt' in job
    assert "run: test -s artifacts/field-comparisons/browser-junit.xml" in job
    assert "name: field-comparisons-browser-${{ github.sha }}" in job
    assert "artifacts/field-comparisons/browser-junit.xml" in job
    assert "retention-days: 14" in job
    # Both final verification and upload execute even when a browser case
    # fails: preserve the exact test names, source SHA and errors for triage.
    assert job.count("if: always()") >= 2
    assert "if-no-files-found: error" in job
