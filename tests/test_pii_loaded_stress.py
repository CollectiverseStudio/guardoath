"""GuardOath issue #30: real local-PII 500-ms load and second-pass release gate.

These are genuine Engine + shipped-policy + local recognizer executions, not a
simulated checker or a larger timeout. Only fixed case identifiers, machine error
codes and elapsed milliseconds enter assertion output; no submitted PII is logged.
An occasional CI failure is a release regression, not a reason to relax the
deadline or the expected findings.
"""

import asyncio
from pathlib import Path
from threading import BoundedSemaphore

import pytest

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

import guardoath.pii as pii
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage

ROOT = Path(__file__).resolve().parents[1]
DOB_CASE = "date_of_birth1"
DOB_CONTENT = "date of birth = 12/31/1990"
REDACT_CONTENT = "DOB: 1980-02-29"


@pytest.fixture(scope="module")
def real_pii_engine():
    shipped = {p.version: p for p in load_policies(ROOT / "policies" / "playground.json")
               if p.version in {"pii-extended-v1", "redaction-v1"}}
    assert set(shipped) == {"pii-extended-v1", "redaction-v1"}
    assert all(p.timeout_ms == 500 for p in shipped.values())
    assert all(p.detect_labeled_sensitive_data and p.privacy_patterns for p in shipped.values())
    return Engine(list(shipped.values()), default_checkers(enable_pii=True))


def _request(version, content):
    return CheckRequest(application_id="demo", policy_version=version,
                        content=content, content_format="text")


def _safe_check_status(result):
    """No customer text, exception text, or transformed content in test failures."""
    return {
        "evaluation_status": result.evaluation_status,
        "recommended_action": result.recommended_action.value,
        "effective_action": result.effective_action.value,
        "redaction_status": result.redaction.status,
        "checkers": [(c.checker_id, c.status, c.error_code, c.latency_ms)
                     for c in result.checker_results],
        "rechecks": [(c.checker_id, c.status, c.error_code, c.latency_ms)
                     for c in result.redaction.validation_results],
        "total_ms": result.total_latency_ms,
    }


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_real_shipped_dob_case_does_not_timeout_under_repeated_shared_worker_load(
        real_pii_engine, stage):
    """The exact previously failing DOB case, 4 concurrent scans x 3 cohorts."""
    request = _request("pii-extended-v1", DOB_CONTENT)

    async def run():
        output = []
        for _ in range(3):
            output.extend(await asyncio.gather(*(
                real_pii_engine.check(request, stage) for _ in range(4))))
        return output

    for result in asyncio.run(run()):
        details = _safe_check_status(result)
        assert result.evaluation_status == "complete", (DOB_CASE, stage.value, details)
        assert result.checker_results[0].status == "flagged", (DOB_CASE, stage.value, details)
        assert "pii.date_of_birth" in {f.rule_id for f in result.findings}, (DOB_CASE, stage.value, details)
        assert result.recommended_action == Action.FLAG, (DOB_CASE, stage.value, details)
        assert result.checker_results[0].latency_ms <= 500, (DOB_CASE, stage.value, details)
        assert "12/31/1990" not in result.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_real_shipped_redaction_requires_clean_second_pass_under_concurrent_load(
        real_pii_engine, stage):
    """A first-pass finding is insufficient; the recheck must pass within 500 ms."""
    request = _request("redaction-v1", REDACT_CONTENT)

    async def run():
        output = []
        for _ in range(3):
            output.extend(await asyncio.gather(*(
                real_pii_engine.check(request, stage) for _ in range(2))))
        return output

    for result in asyncio.run(run()):
        details = _safe_check_status(result)
        assert result.evaluation_status == "complete", (stage.value, details)
        assert "pii.date_of_birth" in {f.rule_id for f in result.findings}, (stage.value, details)
        assert result.redaction.status == "preview", (stage.value, details)
        assert result.redaction.content is not None, (stage.value, details)
        assert "1980-02-29" not in result.redaction.content
        assert result.redaction.validation_results, (stage.value, details)
        assert all(c.status == "passed" and c.latency_ms <= 500
                   for c in result.redaction.validation_results), (stage.value, details)
        assert result.checker_results[0].latency_ms <= 500, (stage.value, details)
        assert "1980-02-29" not in result.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_busy_worker_capacity_returns_incomplete_block_not_clean(
        real_pii_engine, monkeypatch, stage):
    """A capacity failure cannot be silently treated as a clean PII result."""
    monkeypatch.setattr(pii, "_PII_SLOTS", BoundedSemaphore(0))
    enforced = Policy(
        application_id="capacity-gate", version="v1", mode="enforce",
        timeout_ms=500, failure_action="block", detect_labeled_sensitive_data=True,
        rules=(Rule(checker_id="pii", on_finding=Action.REDACT),),
    )
    engine = Engine([enforced], default_checkers(enable_pii=True))
    request = CheckRequest(application_id="capacity-gate", policy_version="v1",
                           content=REDACT_CONTENT, content_format="text")
    result = asyncio.run(engine.check(request, stage))
    assert result.evaluation_status == "incomplete"
    assert result.checker_results[0].status == "error"
    assert result.checker_results[0].error_code == "pii_capacity_exceeded"
    assert result.recommended_action == result.effective_action == Action.BLOCK
    assert result.redaction.status == "withheld"
    assert result.redaction.content is None
    assert not result.findings
    assert REDACT_CONTENT not in result.model_dump_json()
