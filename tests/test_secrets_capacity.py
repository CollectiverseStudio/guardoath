"""Secrets checker capacity isolation and strict deny-on-incomplete regressions."""
import asyncio
from threading import BoundedSemaphore

import pytest

import guardoath.secrets_check as secrets
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Stage


def test_bounded_secret_executor_releases_slot_after_real_worker_completion(monkeypatch):
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(secrets, "_SECRETS_SLOTS", slots)
    assert asyncio.run(secrets._bounded_secret_scan(lambda word: word.upper(), "synthetic")) == "SYNTHETIC"
    # The callback releases capacity when the worker is finished, not when
    # wait_for() cancels the awaiting task.
    assert slots.acquire(blocking=False)
    slots.release()


def test_secret_executor_denies_saturation_without_running_scan(monkeypatch):
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(secrets, "_SECRETS_SLOTS", slots)
    called = []
    async def run():
        with pytest.raises(secrets.SecretsCapacityExceeded):
            await secrets._bounded_secret_scan(lambda: called.append("executed"))
    asyncio.run(run())
    assert not called
    slots.release()


def test_shipped_secrets_policy_blocks_capacity_exhaustion_not_clean(monkeypatch):
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(secrets, "_SECRETS_SLOTS", slots)
    engine = Engine(load_policies("policies/secrets.json"), default_checkers())
    request = CheckRequest(application_id="demo", policy_version="secrets-v1",
                           content="Please explain access key rotation.")
    try:
        result = asyncio.run(engine.check(request, Stage.INPUT))
        assert result.evaluation_status == "incomplete"
        assert result.checker_results[0].status == "error"
        assert result.checker_results[0].error_code == "secrets_capacity_exceeded"
        assert result.recommended_action == "block"
        assert result.findings == ()
    finally:
        slots.release()


def test_shipped_secrets_policy_still_matches_core_token_on_dedicated_executor():
    engine = Engine(load_policies("policies/secrets.json"), default_checkers())
    token = "ghp_" + "A" * 36  # synthetic; never serialized in finding evidence
    request = CheckRequest(application_id="demo", policy_version="secrets-v1", content=token)
    result = asyncio.run(engine.check(request, Stage.INPUT))
    assert result.evaluation_status == "complete"
    assert "secrets.github_token" in {f.rule_id for f in result.findings}
    assert token not in result.model_dump_json()


def test_shipped_500ms_benign_13_source_fixture_repeated_without_timeout():
    """Repeat the exact 2026-11-08 benign.13 CI timeout case, not a proxy."""
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).resolve().parents[1] / "evals" / "secrets.json").read_text())
    case = next(c for c in fixture["cases"] if c["case_id"] == "benign.13")
    assert case["stage"] == "input"
    assert case["request"]["policy_version"] == "secrets-extended-v1"
    policies = load_policies("policies/secrets-batch.json")
    policy = next(p for p in policies if p.version == case["request"]["policy_version"])
    assert policy.timeout_ms == 500
    engine = Engine(policies, default_checkers())
    request = CheckRequest.model_validate(case["request"])
    for trial in range(20):
        result = asyncio.run(engine.check(request, Stage.INPUT))
        statuses = {c.checker_id: c.status for c in result.checker_results}
        assert result.evaluation_status == case["expected"]["evaluation_status"], (trial, statuses)
        assert statuses == case["expected"]["checker_statuses"], (trial, statuses)
        assert result.recommended_action == case["expected"]["recommended_action"]
        assert not result.findings
        assert request.content not in result.model_dump_json()
