"""Privacy executor isolation: a busy unrelated default pool cannot starve local PII.

This synthetic regression isolates queue contention from actual recognizer cost.
It preserves the shipped 500-ms checker deadline, exact redaction and fail-closed
behavior; it does not assert that every real-world timeout is solved.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage


def test_pii_initial_and_redaction_recheck_ignore_blocked_default_thread_pool():
    checkers = default_checkers(enable_pii=True)
    policy = Policy(
        application_id="synthetic", version="v1", mode="enforce", timeout_ms=500,
        rules=(Rule(checker_id="pii", on_finding=Action.REDACT),),
    )
    engine = Engine([policy], checkers)
    request = CheckRequest(
        application_id="synthetic", policy_version="v1",
        content="Contact jane@example.com", content_format="text",
    )

    async def scenario():
        loop = asyncio.get_running_loop()
        default_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="unrelated-busy")
        loop.set_default_executor(default_pool)
        started, release = Event(), Event()

        def unrelated_work():
            started.set()
            release.wait(timeout=4)

        blocked = loop.run_in_executor(None, unrelated_work)
        try:
            for _ in range(200):
                if started.is_set():
                    break
                await asyncio.sleep(0.005)
            assert started.is_set(), "Unrelated default-pool worker did not start"

            result = await engine.check(request, Stage.INPUT)
            pii = next(c for c in result.checker_results if c.checker_id == "pii")
            assert pii.status == "flagged" and pii.error_code is None
            assert {f.rule_id for f in result.findings} == {"pii.email_address"}
            assert result.evaluation_status == "complete"
            assert result.redaction.status == "ready"
            assert result.redaction.content == "Contact [REDACTED_EMAIL_ADDRESS]"
            assert len(result.redaction.validation_results) == 1
            assert result.redaction.validation_results[0].status == "passed"
            assert "jane@example.com" not in result.model_dump_json()
        finally:
            release.set()
            await blocked
            default_pool.shutdown(wait=True)

    asyncio.run(scenario())
