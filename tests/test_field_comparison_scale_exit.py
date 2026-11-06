"""Field Comparisons scale exit status must reflect real schema and worker failures.

All inputs here are synthetic measurement metadata. No external providers/AWS.
"""
import json
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
