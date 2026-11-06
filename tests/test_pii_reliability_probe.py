"""Release reliability probe: private aggregates and real shipped PII contracts."""
import asyncio
import json
from pathlib import Path

import pytest

from tools.pii_reliability_probe import CASE_IDS, SHIPPED_PII_DEADLINE_MS, run_probe, summarize

ROOT = Path(__file__).resolve().parents[1]


def row(case_id, *, matched=True, status="complete", pii="flagged", ms=19.25):
    return {
        "case_id": case_id,
        "stage": "input" if case_id.endswith(".input") else "output",
        "matched": matched,
        "evaluation_status": status,
        "checker_statuses": {"pii": pii},
        "latency_ms": ms,
        # Must never be included in the aggregated output, even if a caller
        # inadvertently supplied source-bearing fields.
        "content": "PRIVATE_NEVER_PRINT_ME",
        "transformed_content": "PRIVATE_NEVER_PRINT_ME",
    }


def report(**changes):
    return {"cases": [row(case_id, **changes.get(case_id, {})) for case_id in CASE_IDS]}


def test_private_aggregate_covers_all_stages_and_redaction_without_raw_content():
    summary = summarize([report(), report()])
    assert summary["accepted"] is True
    assert summary["rounds"] == 2
    assert summary["shipped_checker_deadline_ms"] == SHIPPED_PII_DEADLINE_MS == 500
    assert [c["case_id"] for c in summary["cases"]] == list(CASE_IDS)
    assert {c["stage"] for c in summary["cases"]} == {"input", "output"}
    assert all(c["samples"] == 2 and c["end_to_end_max_ms"] == 19.25 for c in summary["cases"])
    assert "PRIVATE_NEVER_PRINT_ME" not in json.dumps(summary)


def test_incomplete_timeout_and_redaction_mismatch_must_fail_closed():
    second = report(**{
        "date_of_birth1.input": {"matched": False, "status": "incomplete", "pii": "error", "ms": 553.506},
        "redact.enforce.text.input": {"matched": False, "status": "incomplete", "pii": "error", "ms": 639.475},
    })
    summary = summarize([report(), second])
    assert summary["accepted"] is False
    by_id = {item["case_id"]: item for item in summary["cases"]}
    assert by_id["date_of_birth1.input"]["expectation_mismatches"] == 1
    assert by_id["date_of_birth1.input"]["pii_errors"] == 1
    assert by_id["redact.enforce.text.input"]["incomplete"] == 1
    assert by_id["redact.enforce.text.input"]["end_to_end_max_ms"] == 639.475
    assert "PRIVATE_NEVER_PRINT_ME" not in json.dumps(summary)


def test_slow_end_to_end_without_checker_failure_is_diagnostic_not_false_sla_claim():
    summary = summarize([report(**{CASE_IDS[0]: {"ms": 650.0}})])
    assert summary["accepted"] is True
    assert summary["cases"][0]["end_to_end_max_ms"] == 650.0
    assert "not the individual PII checker" in " ".join(summary["notes"])


@pytest.mark.parametrize("reports,ids", [([], CASE_IDS), ([report()], ("duplicate", "duplicate")),
                                         ([{"cases": [row(CASE_IDS[0])]}], CASE_IDS)])
def test_unverifiable_or_incomplete_samples_are_never_accepted(reports, ids):
    with pytest.raises(ValueError):
        summarize(reports, ids)


def test_no_offline_provider_and_existing_shipped_engine_acceptance_both_stages():
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    # This is real Engine/evaluate/Policy/PII/redaction and recheck, not a
    # mock scanner. Two rounds are deliberately small for normal CI;
    # operators run longer independent 20-round measurements before release.
    summary = asyncio.run(run_probe(ROOT / "evals/privacy.json",
                                    ROOT / "policies/privacy-batch.json", 2))
    assert summary["rounds"] == 2
    assert summary["accepted"] is True, json.dumps(summary, sort_keys=True)
    assert all(item["samples"] == 2 and not item["incomplete"]
               and not item["pii_errors"] and not item["expectation_mismatches"]
               for item in summary["cases"])


@pytest.mark.parametrize("rounds", [0, -1, 101])
def test_rejects_invalid_repeat_counts_before_starting_engine(rounds):
    with pytest.raises(ValueError, match="Rounds"):
        asyncio.run(run_probe(ROOT / "evals/privacy.json",
                              ROOT / "policies/privacy-batch.json", rounds))
