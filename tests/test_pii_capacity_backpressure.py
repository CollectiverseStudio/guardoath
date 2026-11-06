"""Deterministic local PII executor backpressure and cancellation contracts.

A timed-out awaiter must never free a slot while its underlying scanner is still
running. Saturation must return an incomplete, private result, not a clean scan.
No real personal information, new providers or changed deadlines are involved.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event

import pytest

import guardoath.pii as pii


def test_full_pii_capacity_refuses_new_work_and_recovers_after_real_completion(monkeypatch):
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="synthetic-capacity")
    monkeypatch.setattr(pii, "_PII_EXECUTOR", executor)
    monkeypatch.setattr(pii, "_PII_SLOTS", BoundedSemaphore(1))
    started, release = Event(), Event()

    def blocked_scanner():
        started.set()
        assert release.wait(timeout=3)
        return "safe-complete"

    async def exercise():
        first = asyncio.create_task(pii._bounded_local_scan(blocked_scanner))
        assert await asyncio.to_thread(started.wait, 1)
        with pytest.raises(pii.PiiCapacityExceeded):
            await pii._bounded_local_scan(lambda: "should-not-run")
        release.set()
        assert await first == "safe-complete"
        assert await pii._bounded_local_scan(lambda: "recovered") == "recovered"

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        executor.shutdown(wait=True)


def test_cancelled_waiter_cannot_release_running_pii_worker_slot(monkeypatch):
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="synthetic-cancel")
    monkeypatch.setattr(pii, "_PII_EXECUTOR", executor)
    monkeypatch.setattr(pii, "_PII_SLOTS", BoundedSemaphore(1))
    started, release = Event(), Event()

    def blocked_scanner():
        started.set()
        assert release.wait(timeout=3)
        return "finished"

    async def exercise():
        waiting = asyncio.create_task(pii._bounded_local_scan(blocked_scanner))
        assert await asyncio.to_thread(started.wait, 1)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        # asyncio.wait_for cancellation must not fake underlying thread completion.
        with pytest.raises(pii.PiiCapacityExceeded):
            await pii._bounded_local_scan(lambda: "should-not-run")
        release.set()
        # Callback is attached to the concurrent.futures Future, and its release
        # must become visible as soon as the *worker*, not the awaiter, finishes.
        for _ in range(200):
            try:
                result = await pii._bounded_local_scan(lambda: "recovered")
                assert result == "recovered"
                break
            except pii.PiiCapacityExceeded:
                await asyncio.sleep(0.005)
        else:
            pytest.fail("Local PII capacity was never recovered after worker completion")

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        executor.shutdown(wait=True)


def test_scanner_exception_and_submission_rejection_return_slot(monkeypatch):
    executor = ThreadPoolExecutor(max_workers=1)
    semaphore = BoundedSemaphore(1)
    monkeypatch.setattr(pii, "_PII_EXECUTOR", executor)
    monkeypatch.setattr(pii, "_PII_SLOTS", semaphore)

    def raises():
        raise RuntimeError("synthetic scanner failure")

    try:
        async def exercise():
            with pytest.raises(RuntimeError, match="synthetic scanner failure"):
                await pii._bounded_local_scan(raises)
            assert await pii._bounded_local_scan(lambda: "recovered") == "recovered"
        asyncio.run(exercise())
    finally:
        executor.shutdown(wait=True)

    class UnavailableExecutor:
        def submit(self, *_args, **_kwargs):
            raise RuntimeError("executor closed")

    monkeypatch.setattr(pii, "_PII_EXECUTOR", UnavailableExecutor())
    with pytest.raises(RuntimeError, match="executor closed"):
        asyncio.run(pii._bounded_local_scan(lambda: None))
    assert semaphore.acquire(blocking=False), "Submission error leaked the PII slot"
    semaphore.release()


def test_authenticated_policy_engine_fails_closed_when_pii_workers_saturated(monkeypatch):
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")

    from guardoath.checkers import default_checkers
    from guardoath.engine import Engine
    from guardoath.models import Action, CheckRequest, Policy, Rule, Stage

    checkers = default_checkers(enable_pii=True)
    policy = Policy(
        application_id="synthetic-privacy", version="v1",
        mode="enforce", timeout_ms=500, failure_action=Action.BLOCK,
        rules=(Rule(checker_id="pii", on_finding=Action.REDACT),),
    )
    engine = Engine([policy], checkers)
    request = CheckRequest(
        application_id="synthetic-privacy", policy_version="v1",
        content="Contact synthetic.person@example.com", content_format="text",
    )
    semaphore = BoundedSemaphore(1)
    assert semaphore.acquire(blocking=False)
    monkeypatch.setattr(pii, "_PII_SLOTS", semaphore)
    try:
        result = asyncio.run(engine.check(request, Stage.INPUT))
    finally:
        semaphore.release()

    assert result.recommended_action == Action.BLOCK
    assert result.effective_action == Action.BLOCK
    assert result.evaluation_status == "incomplete"
    assert result.redaction.status == "withheld"
    assert result.redaction.content is None
    assert len(result.checker_results) == 1
    scanned = result.checker_results[0]
    assert scanned.checker_id == "pii"
    assert scanned.status == "error"
    assert scanned.error_code == "pii_capacity_exceeded"
    assert not result.findings
    assert "synthetic.person@example.com" not in result.model_dump_json()
