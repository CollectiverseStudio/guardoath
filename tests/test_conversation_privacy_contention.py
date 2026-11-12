"""Bounded contention must preserve shipped privacy validation and fail-closed gates."""
import asyncio
import json

import pytest

from tools.conversation_privacy_contention import run_contention


@pytest.mark.parametrize("rounds,workers", [
    (0, 4), (21, 4), (-1, 4), (1.5, 4), (True, 4),
    (1, 0), (1, 5), (1, -1), (1, 2.5), (1, True),
])
def test_contention_rejects_unbounded_or_invalid_workload(rounds, workers):
    with pytest.raises(ValueError):
        asyncio.run(run_contention(rounds, workers))


def test_real_shipped_conversation_under_two_parallel_workers():
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    report = asyncio.run(run_contention(1, 2))
    assert report["accepted"], json.dumps(report, sort_keys=True)
    assert report["shipped_checker_deadline_ms"] == 500
    assert report["concurrency"] == 2 and report["rounds"] == 1
    assert len(report["cases"]) == 8
    assert all(row["samples"] == 2 and row["mismatches"] == 0
               and not row["checker_failures"] for row in report["cases"])
    serialized = json.dumps(report)
    assert "jane@example.com" not in serialized
    assert "synthetic-example!42" not in serialized
