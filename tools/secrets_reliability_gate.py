"""Strict repeated Secrets acceptance at the shipped 500-ms deadline.

Uses existing synthetic suite cases and the real GuardOath Engine. Report
contains only fixed case IDs, counts and numeric timing; no source content.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from math import ceil, isfinite
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    "benign.13",  # Historical 529.512/530.612-ms timeout under shipped policy
    "benign.14",
    "provider.github_token.input",
    "provider.github_token.output",
    "assignment.1",
)
DEADLINE_MS = 500


async def run_gate(rounds: int = 20, concurrency: int = 1):
    if type(rounds) is not int or not 1 <= rounds <= 100:
        raise ValueError("Rounds must be 1..100")
    if type(concurrency) is not int or not 1 <= concurrency <= 4:
        raise ValueError("Concurrency must be 1..4")
    from guardoath.checkers import default_checkers
    from guardoath.config import load_policies
    from guardoath.engine import Engine
    from guardoath.evaluate import evaluate, load_suite

    suite = load_suite(ROOT / "evals/secrets.json")
    selected = tuple(case for case in suite.cases if case.case_id in CASES)
    if len(selected) != len(CASES) or {case.case_id for case in selected} != set(CASES):
        raise ValueError("Required shipped Secrets suite cases missing")
    if any(case.expected.evaluation_status != "complete" for case in selected):
        raise ValueError("Expected cases must be complete")
    policies = load_policies(ROOT / "policies/secrets-batch.json")
    if not policies or any(p.timeout_ms != DEADLINE_MS for p in policies):
        raise ValueError("Shipped 500-ms policy modified")
    engine = Engine(policies, default_checkers())
    if any(not engine.has_policy(c.request.application_id, c.request.policy_version)
           for c in selected):
        raise ValueError("Missing shipped policy")
    subset = suite.model_copy(update={"cases": selected})
    samples = {case: [] for case in CASES}
    for _ in range(rounds):
        # Run each real five-case suite simultaneously across up to four
        # callers, the actual bounded Secrets worker capacity. This exposes
        # contention that sequential cases cannot exercise. Do not add queue
        # slack, retry incomplete results, or change the shipped 500-ms policy.
        batch = await asyncio.gather(*(evaluate(subset, engine)
                                       for _ in range(concurrency)))
        for report in batch:
            if len(report["cases"]) != len(CASES):
                raise ValueError("Incomplete suite evaluation")
            if {row["case_id"] for row in report["cases"]} != set(CASES):
                raise ValueError("Missing/duplicate suite case")
            for row in report["cases"]:
                samples[row["case_id"]].append(row)
    groups = []
    for case in CASES:
        rows = samples[case]
        timings = sorted(float(row["latency_ms"]) for row in rows)
        if any(not isfinite(value) or value < 0 for value in timings):
            raise ValueError("Invalid timing")
        failed = sum(not row["matched"] or row["evaluation_status"] != "complete"
                     or row["checker_statuses"].get("secrets") == "error"
                     or bool(row["checker_diagnostics"]) for row in rows)
        groups.append({
            "case_id": case, "samples": len(rows), "failed_contracts": failed,
            "end_to_end_p95_ms": round(timings[ceil(.95 * len(timings)) - 1], 3),
            "end_to_end_max_ms": round(timings[-1], 3),
        })
    return {"schema_version": 1, "source": "shipped-secrets-suite",
            "shipped_checker_deadline_ms": DEADLINE_MS,
            "rounds": rounds, "concurrency": concurrency, "cases": groups,
            "accepted": all(not row["failed_contracts"] for row in groups),
            "note": "Engine timings are not checker deadlines; green finite samples do not prove tail elimination."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run_gate(args.rounds, args.concurrency))
    except Exception:
        print(json.dumps({"schema_version": 1, "accepted": False, "reason": "gate_unavailable"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
