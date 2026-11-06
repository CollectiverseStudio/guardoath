"""Offline component measurements; never a tenant-limit bypass or release gate.

Run from the checkout: python -m tools.field_comparison_scale --output REPORT.json
Each component/size runs in a bounded child. No network, Engine or model_construct.
"""
import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from types import SimpleNamespace

from pydantic import ValidationError

from guardoath.business import cross_field_check
from guardoath.models import CheckRequest, FieldComparison, Policy, Rule, Stage
from guardoath.record_rules import record_rules
from guardoath.tenant_record import TenantRecord

SIZES = (30, 51, 100, 250, 500)


def portfolio(size):
    """Independent synthetic pointers: all comparisons have a feasible joint witness."""
    return tuple(FieldComparison(rule_id=f"scale_{i}", left=f"/left_{i}",
                                 right=f"/right_{i}", operator="le") for i in range(size))


def schema_acceptance(comparisons):
    results = {}
    for name, model, fields in (
        ("policy", Policy, {"application_id": "scale", "version": "v1", "rules":
                           (Rule(checker_id="cross_field"),)}),
        ("tenant_record", TenantRecord, {"tenant_id": "scale", "display_name": "Scale",
                                       "input_check_cross_field": "block",
                                       "output_check_cross_field": "block"}),
    ):
        try:
            model(**fields, comparisons=comparisons)
        except ValidationError as error:
            # Validation input may contain submitted values; retain only paths/types.
            results[name] = {"accepted": False, "errors": [
                {"path": list(e["loc"]), "type": e["type"]} for e in error.errors()
            ]}
        else:
            results[name] = {"accepted": True, "errors": []}
    return results


def measure(component, size, samples):
    comparisons = portfolio(size)
    holder = SimpleNamespace(comparisons=comparisons, required_sections=())
    payload = {key: value for i in range(size)
               for key, value in ((f"left_{i}", 1), (f"right_{i}", 2))}
    content = json.dumps(payload)
    request = CheckRequest(application_id="scale", policy_version="v1",
                           content=content, content_format="json")
    durations = []
    tracemalloc.start()
    output_bytes = 0
    for _ in range(samples):
        start = time.perf_counter()
        if component == "checker":
            for stage in (Stage.INPUT, Stage.OUTPUT):
                result = asyncio.run(cross_field_check(request, holder, stage))
                if result.status != "passed" or result.findings:
                    raise ValueError("Synthetic joint witness failed")
            output_bytes = len(content.encode("utf-8"))
        else:
            rows = record_rules(holder)
            if len(rows) != size:
                raise ValueError("Generated catalog count mismatch")
            output_bytes = len(json.dumps(rows).encode("utf-8"))
        durations.append((time.perf_counter() - start) * 1000)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    ordered = sorted(durations)
    return {"component": component, "size": size, "status": "measured",
            "samples": samples, "median_ms": statistics.median(durations),
            "p95_nearest_rank_ms": ordered[math.ceil(.95 * samples) - 1],
            "peak_traced_bytes": peak, "output_bytes": output_bytes,
            "scope": "standalone component; not Engine or tenant acceptance",
            "schema_acceptance": schema_acceptance(comparisons)}


def bounded_measure(component, size, samples, timeout):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "tools.field_comparison_scale", "--worker", component,
             "--size", str(size), "--samples", str(samples)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"component": component, "size": size, "status": "timeout",
                "timeout_seconds": timeout}
    if result.returncode:
        return {"component": component, "size": size, "status": "worker_error",
                "returncode": result.returncode}
    return json.loads(result.stdout)



def scale_acceptance(measurements, sizes=SIZES, components=("checker", "generator")):
    """A scale run is accepted only if every requested checker and generator measurement succeeded.

    A subprocess timeout, an error, an omitted sample, an unexpected size, or a
    Policy/TenantRecord schema refusal is a real failure, not a successful report.
    This does NOT imply the independent portfolio, API, UI, or production gates passed.
    """
    expected = {(size, component) for size in sizes for component in components}
    seen = set()
    for row in measurements:
        if not isinstance(row, dict):
            return False
        key = (row.get("size"), row.get("component"))
        if key not in expected or key in seen:
            return False
        seen.add(key)
        if row.get("status") != "measured" or not isinstance(row.get("samples"), int) or row["samples"] < 1:
            return False
        schema = row.get("schema_acceptance")
        if not isinstance(schema, dict):
            return False
        if any(not isinstance(schema.get(name), dict) or schema[name].get("accepted") is not True
               or schema[name].get("errors") for name in ("policy", "tenant_record")):
            return False
        if any(not isinstance(row.get(field), (int, float)) or not math.isfinite(row[field])
               or row[field] < 0 for field in ("median_ms", "p95_nearest_rank_ms",
                                              "peak_traced_bytes", "output_bytes")):
            return False
    return seen == expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=("checker", "generator"))
    parser.add_argument("--size", type=int, choices=SIZES, default=30)
    parser.add_argument("--samples", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--timeout", type=int, choices=range(1, 31), default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.worker:
        report = measure(args.worker, args.size, args.samples)
    else:
        report = {"schema_version": 1, "python": platform.python_version(),
                  "platform": platform.platform(), "release_readiness": False,
                  "source_sha256": {name: hashlib.sha256(
                      (Path(__file__).resolve().parents[1] / name).read_bytes()).hexdigest()
                      for name in ("guardoath/business.py", "guardoath/record_rules.py",
                                   "guardoath/models.py", "guardoath/tenant_record.py",
                                   "tools/field_comparison_scale.py")},
                  "notes": ["Synthetic independent numeric pointers, both stages per checker sample.",
                            "tracemalloc adds overhead; peak excludes model setup and is not process RSS.",
                            "Generator samples measure construction, not full-portfolio benign correctness.",
                            "Nearest-rank p95 of a small sample is not a stable tail-latency estimate.",
                            "Timeouts are evidence gaps, not passing measurements or product limits.",
                            "No concurrency, request admission, persisted API or deployed acceptance tested."],
                  "measurements": [bounded_measure(c, n, args.samples, args.timeout)
                                   for n in SIZES for c in ("checker", "generator")]}
    # Tool process status is an acceptance signal. Historically the scale tool
    # returned success even when 51+ comparisons failed schema admission or
    # generator workers timed out. Do not weaken budgets or claim release readiness.
    accepted = scale_acceptance([report] if args.worker else report["measurements"],
                                (args.size,) if args.worker else SIZES,
                                (args.worker,) if args.worker else ("checker", "generator"))
    if not args.worker:
        report["scale_acceptance_passed"] = accepted
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
