"""Release-gate repeat coverage for EVERY previously observed local PII tail case.

Use the checked-in synthetic privacy and privacy-patterns suites and their
existing tenant policies and real Engine, including mandatory REDACT rechecks.
Only fixed case IDs and timing/status counters are printed. No customer data.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from tools.pii_reliability_probe import summarize

ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    # The all-suite collection's exact observed tail regression (2026-11-08):
    # the initial email scan flags, then the mandatory redaction recheck can
    # hit the unchanged 500-ms budget under runner/worker contention.
    # Exercise the same source case/policy via the real Engine on every round.
    ("local", "evals/local.json", "policies/playground.json", (
        "redaction.email",
    )),
    ("privacy", "evals/privacy.json", "policies/privacy-batch.json", (
        # 2026-11-08 local-pii (3.13) job 128738797961: observed 540.484ms
        # checker_timeout on ssn.input, despite the same 500ms policy.
        "ssn.input",
        "phone_number1.input", "phone_number1.output",
        "date_of_birth1.input", "date_of_birth1.output",
        "redact.enforce.text.input", "redact.enforce.text.output",
    )),
    ("privacy-patterns", "evals/privacy-patterns.json",
     "policies/privacy-patterns-batch.json", (
         "apac.kr-rrn.input", "apac.kr-rrn.output",
     )),
)


async def run_matrix(rounds=20):
    if type(rounds) is not int or not 1 <= rounds <= 100:
        raise ValueError("Rounds must be between 1 and 100")
    from guardoath.checkers import default_checkers
    from guardoath.config import load_policies
    from guardoath.engine import Engine
    from guardoath.evaluate import evaluate, load_suite

    groups = []
    for label, suite_path, policy_path, case_ids in SUITES:
        suite = load_suite(ROOT / suite_path)
        wanted = set(case_ids)
        selected = tuple(case for case in suite.cases if case.case_id in wanted)
        if len(selected) != len(case_ids) or {case.case_id for case in selected} != wanted:
            raise ValueError("A required synthetic PII case is absent")
        if any(case.expected.evaluation_status != "complete" for case in selected):
            raise ValueError("PII acceptance requires complete expected outcomes")
        engine = Engine(load_policies(ROOT / policy_path), default_checkers(enable_pii=True))
        if any(not engine.has_policy(case.request.application_id, case.request.policy_version)
               for case in selected):
            raise ValueError("PII case requires a missing policy")
        subset = suite.model_copy(update={"cases": selected})
        reports = [await evaluate(subset, engine) for _ in range(rounds)]
        result = summarize(reports, case_ids)
        groups.append({"suite": label, "accepted": result["accepted"], "cases": result["cases"]})
    return {
        "accepted": all(group["accepted"] for group in groups),
        "rounds": rounds,
        "shipped_checker_deadline_ms": 500,
        "groups": groups,
        "notes": (
            "Synthetic fixed case IDs and aggregate statuses only; no source contents.",
            "This includes observed 505.215ms phone, 542.812ms Korean RRN, 540.484ms SSN, DOB, and redaction.email recheck tails.",
            "End-to-end timing is diagnostic, not the individual checker deadline.",
            "A green finite sample alone cannot prove permanent latency resolution.",
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(run_matrix(args.rounds))
    except Exception:
        # Do not serialize arbitrary exception text from privacy recognizers.
        print("PII release matrix unavailable: verify local dependencies and synthetic evaluation fixtures.")
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
