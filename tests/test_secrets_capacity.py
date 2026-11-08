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
