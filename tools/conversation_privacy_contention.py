"""Reproduce shipped conversation privacy checks under bounded concurrent load.

Each worker executes the actual local four-check Input/Output profile. Multiple
workers contend for the same bounded PII executor and the unchanged 500-ms
checker deadline. No provider call or raw content enters the report.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from tools.conversation_privacy_release import run_conversation_matrix


async def run_contention(rounds: int = 10, concurrency: int = 4):
    if type(rounds) is not int or not 1 <= rounds <= 20:
        raise ValueError("Rounds must be an integer between 1 and 20")
    if type(concurrency) is not int or not 1 <= concurrency <= 4:
        raise ValueError("Concurrency must be an integer between 1 and 4")
    # A batch has concurrent independent Engines sharing the real PII worker
    # pool. Always await the full batch; do not retry failed samples.
    case_counts = {}
    for _ in range(rounds):
        batch = await asyncio.gather(*(
            run_conversation_matrix(1) for _worker in range(concurrency)
        ))
        for report in batch:
            for case in report["cases"]:
                entry = case_counts.setdefault(case["case_id"], {
                    "case_id": case["case_id"], "samples": 0,
                    "mismatches": 0, "checker_failures": {},
                })
                entry["samples"] += case["samples"]
                entry["mismatches"] += case["mismatches"]
                for code, count in case["checker_failures"].items():
                    # Labels are already allowlisted by the underlying runner.
                    entry["checker_failures"][code] = (
                        entry["checker_failures"].get(code, 0) + count
                    )
    cases = [case_counts[key] for key in sorted(case_counts)]
    if len(cases) != 8 or any(c["samples"] != rounds * concurrency for c in cases):
        raise ValueError("Incomplete conversation profile coverage")
    return {
        "accepted": all(c["mismatches"] == 0 for c in cases),
        "rounds": rounds, "concurrency": concurrency,
        "shipped_checker_deadline_ms": 500, "cases": cases,
        "note": "Fixed synthetic case IDs and allowlisted status/error codes only. "
                "Concurrent green samples do not establish permanent latency safety.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(run_contention(args.rounds, args.concurrency))
    except Exception:
        print("Conversation privacy contention gate unavailable; inspect installed local dependencies.")
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
