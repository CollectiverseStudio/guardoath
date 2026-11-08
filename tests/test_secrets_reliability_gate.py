"""Strict Secrets release-gate inventory and original 500-ms Engine acceptance."""
import asyncio
import json

import pytest

from tools.secrets_reliability_gate import CASES, DEADLINE_MS, run_gate


def test_gate_preserves_historical_benign_timeout_and_positive_outcomes():
    assert CASES == (
        "benign.13", "benign.14",
        "provider.github_token.input", "provider.github_token.output",
        "assignment.1",
    )
    assert DEADLINE_MS == 500
    result = asyncio.run(run_gate(1))
    assert result["accepted"], json.dumps(result, sort_keys=True)
    assert result["shipped_checker_deadline_ms"] == 500
    assert [item["case_id"] for item in result["cases"]] == list(CASES)
    assert all(item["samples"] == 1 and item["failed_contracts"] == 0 for item in result["cases"])
    assert "glpat-short" not in json.dumps(result)
    assert "password" not in json.dumps(result)


@pytest.mark.parametrize("rounds", [0, -1, 101, 1.5, True])
def test_rejects_invalid_rounds_without_running_checks(rounds):
    with pytest.raises(ValueError):
        asyncio.run(run_gate(rounds))


def test_bounded_four_caller_contention_preserves_every_original_finding():
    # Four simultaneous real suite evaluators, each with unchanged 500ms
    # per-check timeout. Not a larger deadline or a mock detector.
    result = asyncio.run(run_gate(1, concurrency=4))
    assert result["accepted"], json.dumps(result, sort_keys=True)
    assert result["concurrency"] == 4
    assert result["shipped_checker_deadline_ms"] == 500
    assert all(item["samples"] == 4 and item["failed_contracts"] == 0
               for item in result["cases"])
    assert [item["case_id"] for item in result["cases"]] == list(CASES)


@pytest.mark.parametrize("concurrency", [0, -1, 5, 1.5, True])
def test_concurrency_cannot_exceed_existing_worker_bound(concurrency):
    with pytest.raises(ValueError):
        asyncio.run(run_gate(1, concurrency=concurrency))
