"""Measure shipped local PII initial/recheck reliability without logging message data.

Run from repository root with existing PII dependencies:
    python -m tools.pii_latency_probe --rounds 10 --concurrency 4

Only synthetic, hard-coded DOB samples are used. Output contains fixed case IDs,
stage, counts and bounded numeric timing summaries; never prompts, findings,
transformed text, exceptions or credentials. Exit 2 if any scan is incomplete,
late or returns a wrong contract. This is diagnostic evidence, not an SLA proof.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from statistics import median

_CASES = (
    ("dob-detection", "pii-extended-v1", "date of birth = 12/31/1990"),
    ("dob-redaction", "redaction-v1", "DOB: 1980-02-29"),
)


def summarize(case_id, stage, results):
    """Aggregate *only* known-safe metrics; do not serialize CheckResponse."""
    latencies, recheck_latencies = [], []
    errors = 0
    for result in results:
        checkers = tuple(result.checker_results)
        rechecks = tuple(result.redaction.validation_results)
        latencies.extend(float(c.latency_ms) for c in checkers if c.latency_ms is not None)
        recheck_latencies.extend(float(c.latency_ms) for c in rechecks if c.latency_ms is not None)
        is_bad = result.evaluation_status != "complete" or any(c.status == "error" for c in checkers + rechecks)
        if case_id == "dob-detection":
            is_bad |= not any(f.rule_id == "pii.date_of_birth" for f in result.findings)
        else:
            is_bad |= (result.redaction.status != "preview" or
                       not rechecks or any(c.status != "passed" for c in rechecks) or
                       result.redaction.content is None)
        is_bad |= any(c.latency_ms is not None and c.latency_ms > 500 for c in checkers + rechecks)
        errors += bool(is_bad)
    def durations(values):
        ordered = sorted(values)
        return {"observed": len(ordered), "median_ms": round(median(ordered), 3) if ordered else None,
                "max_ms": round(ordered[-1], 3) if ordered else None}
    return {"case": case_id, "stage": stage, "requests": len(results),
            "failed_contracts": errors, "first_pass": durations(latencies),
            "redaction_recheck": durations(recheck_latencies)}


async def probe(rounds, concurrency):
    from guardoath.checkers import default_checkers
    from guardoath.config import load_policies
    from guardoath.engine import Engine
    from guardoath.models import CheckRequest, Stage

    root = Path(__file__).resolve().parents[1]
    policies = [p for p in load_policies(root / "policies" / "playground.json")
                if p.version in {case[1] for case in _CASES}]
    if len(policies) != 2 or any(p.timeout_ms != 500 for p in policies):
        raise RuntimeError("Shipped PII policies do not have the required 500-ms contract")
    engine = Engine(policies, default_checkers(enable_pii=True))
    reports = []
    for case_id, version, content in _CASES:
        request = CheckRequest(application_id="demo", policy_version=version,
                               content=content, content_format="text")
        for stage in (Stage.INPUT, Stage.OUTPUT):
            results = []
            for _ in range(rounds):
                results.extend(await asyncio.gather(*(engine.check(request, stage)
                                                      for _ in range(concurrency))))
            reports.append(summarize(case_id, stage.value, results))
    return {"schema_version": 1, "source": "shipped-local-pii-synthetic",
            "rounds": rounds, "concurrency": concurrency, "500_ms_unchanged": True,
            "results": reports, "all_contracts_passed": all(not r["failed_contracts"] for r in reports)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args(argv)
    if not 1 <= args.rounds <= 100 or not 1 <= args.concurrency <= 4:
        parser.error("rounds must be 1..100 and concurrency must be 1..4")
    try:
        report = asyncio.run(probe(args.rounds, args.concurrency))
    except Exception:
        # Never reveal potentially private exception text or stack to output.
        print(json.dumps({"schema_version": 1, "all_contracts_passed": False,
                          "reason": "probe_unavailable"}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["all_contracts_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
