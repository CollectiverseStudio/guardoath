"""Repeat the exact four-check conversation profile on both stages.

The prior Windows 3.12 failure in tests/test_dashboard.py required a complete
evaluation but received fail-closed incomplete. This separate source-backed
release gate exercises the shipped profile, not a simulated privacy adapter.
Only fixed case IDs and checker error-code/status metadata are reported.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {"pii", "secrets", "text_security", "encoded_pii"}
FIXTURES = (
    ("benign", "Explain returns.", None),
    ("email", "Email jane@example.com", "pii"),
    ("secret", 'password = "synthetic-example!42"', "secrets"),
    ("unicode", "Hidden\u200bcharacter", "text_security"),
)


async def run_conversation_matrix(rounds: int = 20):
    if type(rounds) is not int or not 1 <= rounds <= 100:
        raise ValueError("Rounds must be an integer between 1 and 100")
    from guardoath.checkers import default_checkers
    from guardoath.config import load_policies
    from guardoath.engine import Engine
    from guardoath.models import CheckRequest, Stage

    engine = Engine(load_policies(ROOT / "policies/playground.json"),
                    default_checkers(enable_pii=True))
    if not engine.has_policy("demo", "conversation-v1"):
        raise ValueError("Shipped conversation profile missing")
    cases = []
    accepted = True
    for stage in (Stage.INPUT, Stage.OUTPUT):
        for name, content, flagged in FIXTURES:
            mismatches = 0
            failures = {}
            for _ in range(rounds):
                response = await engine.check(CheckRequest(
                    application_id="demo", policy_version="conversation-v1",
                    content=content, content_format="text"), stage)
                receipts = {item.checker_id: item for item in response.checker_results}
                expected_action = ("allow" if flagged is None
                                   else ("flag" if stage == Stage.INPUT else "block"))
                if (response.evaluation_status != "complete"
                        or set(receipts) != REQUIRED
                        or any(item.simulated for item in receipts.values())
                        or any(item.status not in {"passed", "flagged"}
                               for item in receipts.values())
                        or (flagged is not None and
                            receipts.get(flagged) is not None and
                            receipts[flagged].status != "flagged")
                        or (flagged is None and
                            any(item.status != "passed" for item in receipts.values()))
                        or response.recommended_action.value != expected_action):
                    mismatches += 1
                for checker_id, receipt in receipts.items():
                    if receipt.status not in {"passed", "flagged"}:
                        key = checker_id + ":" + (receipt.error_code or receipt.status)
                        failures[key] = failures.get(key, 0) + 1
            cases.append({"case_id": stage.value + "." + name,
                          "samples": rounds, "mismatches": mismatches,
                          "checker_failures": failures})
            accepted = accepted and mismatches == 0
    return {"accepted": accepted, "rounds": rounds,
            "shipped_checker_deadline_ms": 500, "cases": cases,
            "note": "Synthetic fixture IDs and checker status/error codes only. "
                    "Passing finite rounds does not resolve intermittent historical failures."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run_conversation_matrix(args.rounds))
    except Exception:
        print("Conversation privacy reliability gate unavailable; verify offline optional dependencies.")
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
