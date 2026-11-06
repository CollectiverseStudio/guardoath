"""Repeat shipped PII cases through the real GuardOath Engine without providers.

Only case IDs, stages, expectation counts, checker error counts and aggregate
latency are reported. Raw requests, generated redactions and finding values
must never be written to the report or to stdout.

Use:
  python -m tools.pii_reliability_probe --rounds 20

This is a tail-latency diagnostic and repeated acceptance gate, not a claim
that a green sample proves the intermittent 500-ms incident resolved.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from math import ceil
from pathlib import Path
from statistics import median

# These are synthetic examples from the checked-in privacy suite. Both Input
# and Output, ordinary local detection and enforce-mode redaction recheck are
# required. Do not substitute an unvalidated benchmark-only detector.
CASE_IDS = (
    "date_of_birth1.input",
    "date_of_birth1.output",
    "redact.enforce.text.input",
    "redact.enforce.text.output",
)
SHIPPED_PII_DEADLINE_MS = 500


def summarize(reports, expected_case_ids=CASE_IDS):
    """Aggregate evaluator metadata, never serialized request/response fields."""
    ids = tuple(expected_case_ids)
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError("Expected case IDs must be distinct and nonempty")
    groups = {case_id: [] for case_id in ids}
    for report in reports:
        rows = report["cases"]
        if len(rows) != len(ids) or {row["case_id"] for row in rows} != set(ids):
            raise ValueError("Every round must contain exactly the approved cases")
        for row in rows:
            groups[row["case_id"]].append(row)
    if not reports:
        raise ValueError("At least one round is required")
    details = []
    for case_id in ids:
        rows = groups[case_id]
        timings = sorted(float(row["latency_ms"]) for row in rows)
        # End-to-end Engine latency is NOT the internal checker deadline.
        # A slow Engine call alone is diagnostic; mismatched shipped policy
        # expectations, incomplete results or PII errors are hard failures.
        bad = sum(not row["matched"] for row in rows)
        incomplete = sum(row["evaluation_status"] != "complete" for row in rows)
        pii_errors = sum(row["checker_statuses"].get("pii") == "error" for row in rows)
        details.append({
            "case_id": case_id,
            "stage": rows[0]["stage"],
            "samples": len(rows),
            "expectation_mismatches": bad,
            "incomplete": incomplete,
            "pii_errors": pii_errors,
            "end_to_end_median_ms": round(median(timings), 3),
            "end_to_end_p95_nearest_rank_ms": round(timings[ceil(.95 * len(timings)) - 1], 3),
            "end_to_end_max_ms": round(timings[-1], 3),
        })
    return {
        "scope": "real_local_engine_existing_privacy_suite",
        "shipped_checker_deadline_ms": SHIPPED_PII_DEADLINE_MS,
        "rounds": len(reports),
        "cases": details,
        "accepted": all(not (case["expectation_mismatches"] or case["incomplete"]
                             or case["pii_errors"]) for case in details),
        "notes": (
            "Only synthetic case IDs and outcome metadata; no submitted or redacted content.",
            "Measured Engine end-to-end latency is not the individual PII checker elapsed time.",
            "A green finite sample does not resolve prior intermittent 500-ms PII timeouts.",
        ),
    }


async def run_probe(suite_path: Path, policies_path: Path, rounds: int):
    """Use exactly the shipped evaluator/Engine expectations and privacy policies."""
    if not 1 <= rounds <= 100:
        raise ValueError("Rounds must be between 1 and 100")
    from guardoath.checkers import default_checkers
    from guardoath.config import load_policies
    from guardoath.engine import Engine
    from guardoath.evaluate import evaluate, load_suite

    suite = load_suite(suite_path)
    selected = tuple(case for case in suite.cases if case.case_id in CASE_IDS)
    if len(selected) != len(CASE_IDS) or {case.case_id for case in selected} != set(CASE_IDS):
        raise ValueError("The existing privacy suite must contain every required case")
    if any(case.expected.evaluation_status != "complete" for case in selected):
        raise ValueError("The required privacy cases must expect complete evaluations")
    engine = Engine(load_policies(policies_path), default_checkers(enable_pii=True))
    if any(not engine.has_policy(case.request.application_id, case.request.policy_version)
           for case in selected):
        raise ValueError("The selected policy versions must exist")
    subset = suite.model_copy(update={"cases": selected})
    reports = []
    for _ in range(rounds):
        reports.append(await evaluate(subset, engine))
    return summarize(reports)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=20, help="Independent repeats (1-100); 20 recommended")
    parser.add_argument("--suite", default="evals/privacy.json")
    parser.add_argument("--policies", default="policies/privacy-batch.json")
    args = parser.parse_args(argv)
    try:
        summary = asyncio.run(run_probe(Path(args.suite), Path(args.policies), args.rounds))
    except Exception:
        # Never print exceptions; external libraries can include original text.
        print('Unable to execute local PII reliability probe: check privacy suite, policies and optional PII dependencies.')
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
