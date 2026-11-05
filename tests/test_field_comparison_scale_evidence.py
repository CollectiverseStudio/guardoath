"""Evidence must distinguish component behavior, schema admission and missing data."""
import asyncio
from types import SimpleNamespace
import subprocess

import pytest

from guardoath.business import cross_field_check
from guardoath.models import CheckRequest, Stage
from tools import field_comparison_scale as evidence


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_large_standalone_witness_detects_all_violations_with_original_ids(stage):
    import json
    comparisons = evidence.portfolio(51)
    holder = SimpleNamespace(comparisons=comparisons)
    payload = {key: value for i in range(51)
               for key, value in ((f"left_{i}", 3), (f"right_{i}", 2))}
    request = CheckRequest(application_id="scale", policy_version="v1",
                           content=json.dumps(payload), content_format="json")
    result = asyncio.run(cross_field_check(request, holder, stage))
    assert result.status == "flagged"
    assert [f.rule_id for f in result.findings] == [f"business.scale_{i}" for i in range(51)]


def test_checker_measurement_separates_complete_benign_from_tenant_admission():
    report = evidence.measure("checker", 51, 2)
    assert report["status"] == "measured" and report["samples"] == 2
    assert report["output_bytes"] > 0 and report["peak_traced_bytes"] > 0
    assert 0 <= report["median_ms"] <= report["p95_nearest_rank_ms"]
    assert "not Engine or tenant acceptance" in report["scope"]
    # Report the real schema outcome, never substitute the standalone holder for it.
    assert report["schema_acceptance"] == evidence.schema_acceptance(evidence.portfolio(51))
    for admission in report["schema_acceptance"].values():
        for error in admission["errors"]:
            assert set(error) == {"path", "type"}


def test_generator_records_actual_output_not_a_benign_acceptance_claim():
    report = evidence.measure("generator", 3, 1)
    assert report["status"] == "measured" and report["output_bytes"] > 0
    assert report["size"] == 3 and "benign_passed" not in report


def test_timeout_has_no_fabricated_latency_or_success(monkeypatch):
    def expire(*args, **kwargs):
        raise subprocess.TimeoutExpired("worker", 1)
    monkeypatch.setattr(evidence.subprocess, "run", expire)
    report = evidence.bounded_measure("generator", 500, 3, 1)
    assert report == {"component": "generator", "size": 500,
                      "status": "timeout", "timeout_seconds": 1}


def test_worker_error_does_not_echo_captured_text(monkeypatch):
    def fail(*args, **kwargs):
        return SimpleNamespace(returncode=1, stderr="private marker", stdout="private marker")
    monkeypatch.setattr(evidence.subprocess, "run", fail)
    report = evidence.bounded_measure("checker", 30, 1, 1)
    assert report == {"component": "checker", "size": 30,
                      "status": "worker_error", "returncode": 1}


def test_real_child_reports_both_schema_acceptance_and_measurement():
    report = evidence.bounded_measure("checker", 30, 1, 10)
    assert report["status"] == "measured"
    assert all(row["accepted"] for row in report["schema_acceptance"].values())
