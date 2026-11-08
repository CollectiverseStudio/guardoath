"""Privacy-safe Engine release contract: scanner failure cannot become observed Allow.

Synthetic local checkers deterministically trigger the same Engine error paths as
PII/encoded PII/Secrets deadline and capacity failures. The existing real 500-ms
recognizer stress tests remain the latency release gate; these tests do not mock
the Engine or suppress independent findings.
"""
import asyncio

import pytest

from guardoath.engine import Engine
from guardoath.models import (
    Action, CheckRequest, CheckerResult, Finding, Policy, Rule, Stage,
)


class _FixedChecker:
    checker_version = "synthetic-test-v1"
    simulated = False

    def __init__(self, result):
        self.result = result

    async def __call__(self, request, policy, stage):
        return self.result


class _PiiRecheckFailure:
    checker_version = "synthetic-test-v1"
    simulated = False

    def __init__(self):
        self.calls = 0

    async def __call__(self, request, policy, stage):
        self.calls += 1
        if self.calls == 1:
            return CheckerResult(
                checker_id="pii", checker_version=self.checker_version,
                status="flagged",
                findings=(Finding(rule_id="pii.email_address", category="privacy",
                                  severity="high", explanation="Synthetic privacy finding"),),
                transformed_content="REDACTED SYNTHETIC CONTENT",
            )
        return CheckerResult(checker_id="pii", checker_version=self.checker_version,
                             status="error", error_code="pii_scan_deadline")


def _run(checker_id, result, stage, mode="observe", privacy_action=Action.FLAG):
    other_finding = Finding(rule_id="text_security.synthetic_guard", category="text_security",
                            severity="low", explanation="Independent synthetic finding")
    policy = Policy(
        application_id="privacy-failure-gate", version="v1", mode=mode,
        failure_action="flag", timeout_ms=500,
        rules=(
            Rule(checker_id=checker_id, on_finding=privacy_action),
            Rule(checker_id="text_security", on_finding=Action.FLAG),
        ),
    )
    engine = Engine([policy], {
        checker_id: _FixedChecker(result),
        "text_security": _FixedChecker(
            CheckerResult(checker_id="text_security", checker_version="synthetic-test-v1",
                          status="flagged", findings=(other_finding,))),
    })
    request = CheckRequest(application_id=policy.application_id,
                           policy_version=policy.version, content="synthetic safe example")
    return asyncio.run(engine.check(request, stage))


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("checker_id", ["pii", "encoded_pii", "secrets"])
@pytest.mark.parametrize("error_code", ["checker_timeout", "checker_failure"])
@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_failed_enabled_privacy_scan_blocks_even_with_tenant_flag(stage, checker_id, error_code, mode):
    result = _run(checker_id,
                  CheckerResult(checker_id=checker_id, checker_version="synthetic-test-v1",
                                status="error", error_code=error_code),
                  stage, mode=mode)
    assert result.evaluation_status == "incomplete"
    assert result.recommended_action == Action.BLOCK
    assert result.effective_action == Action.BLOCK
    assert {(c.checker_id, c.status, c.error_code) for c in result.checker_results} == {
        (checker_id, "error", error_code), ("text_security", "flagged", None),
    }
    assert {f.rule_id for f in result.findings} == {"text_security.synthetic_guard"}
    assert result.redaction.content is None
    assert "synthetic safe example" not in result.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("status", ["skipped", "unsupported"])
def test_enabled_privacy_scanner_unavailable_is_not_a_pass(stage, status):
    result = _run("pii",
                  CheckerResult(checker_id="pii", checker_version="synthetic-test-v1",
                                status=status, error_code="checker_unavailable"), stage)
    assert result.evaluation_status == "incomplete"
    assert result.recommended_action == result.effective_action == Action.BLOCK


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_completed_privacy_findings_keep_normal_tenant_actions(stage, mode):
    result = _run("pii",
                  CheckerResult(checker_id="pii", checker_version="synthetic-test-v1",
                                status="flagged",
                                findings=(Finding(rule_id="pii.email_address",
                                                  category="privacy", severity="high",
                                                  explanation="Synthetic privacy finding"),)),
                  stage, mode=mode)
    assert result.evaluation_status == "complete"
    assert result.recommended_action == Action.FLAG
    assert result.effective_action == (Action.ALLOW if mode == "observe" else Action.FLAG)
    assert {f.rule_id for f in result.findings} == {
        "pii.email_address", "text_security.synthetic_guard",
    }


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_redaction_failed_mandatory_recheck_blocks_observe_mode(stage):
    checker = _PiiRecheckFailure()
    policy = Policy(
        application_id="privacy-recheck-gate", version="v1", mode="observe",
        failure_action="flag", timeout_ms=500,
        rules=(Rule(checker_id="pii", on_finding=Action.REDACT),),
    )
    engine = Engine([policy], {"pii": checker})
    request = CheckRequest(application_id=policy.application_id,
                           policy_version=policy.version, content="synthetic@example.test")
    result = asyncio.run(engine.check(request, stage))
    assert checker.calls == 2
    assert result.evaluation_status == "incomplete"
    assert result.recommended_action == result.effective_action == Action.BLOCK
    assert result.redaction.status == "withheld"
    assert result.redaction.content is None
    assert len(result.redaction.validation_results) == 1
    assert result.redaction.validation_results[0].error_code == "pii_scan_deadline"
    assert {f.rule_id for f in result.findings} == {"pii.email_address"}
    assert "synthetic@example.test" not in result.model_dump_json()
