"""Real Engine second-pass PII error attribution without content exposure.

The first pass can flag an identifier while mandatory redaction revalidation
times out. Reporting only first-pass checker errors hides that failure.
No test overrides the 500-ms policy or replaces the Engine/recheck path.
"""
import asyncio
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from guardoath.compare import Report, compare_reports
from guardoath.engine import Engine
from guardoath.evaluate import EvaluationCase, Expected, Suite, evaluate, safe_checker_diagnostics
from guardoath.models import Action, CheckRequest, CheckerResult, Finding, Policy, Rule, Stage

PRIVATE = "PRIVATE_SYNTHETIC_PERSON_NEVER_PUBLISH@example.com"
REPLACEMENT = "[REDACTED_EMAIL_ADDRESS]"


def _engine_and_suite(stage, *, recheck_code):
    async def pii_checker(request, _policy, _stage):
        if request.content == REPLACEMENT:
            return CheckerResult(
                checker_id="pii", checker_version="synthetic-private-recheck-1",
                status="error" if recheck_code else "passed",
                error_code=recheck_code,
            )
        assert request.content == PRIVATE
        return CheckerResult(
            checker_id="pii", checker_version="synthetic-private-recheck-1",
            status="flagged",
            findings=(Finding(
                rule_id="pii.email_address", category="privacy", severity="high",
                explanation="Potential email identifier detected."
            ),),
            transformed_content=REPLACEMENT,
        )

    policy = Policy(
        application_id="synthetic-recheck", version="v1", mode="enforce",
        timeout_ms=500, failure_action="block",
        rules=(Rule(checker_id="pii", on_finding=Action.REDACT),),
    )
    engine = Engine([policy], {"pii": pii_checker})
    request = CheckRequest(
        application_id="synthetic-recheck", policy_version="v1",
        content=PRIVATE, content_format="text",
    )
    case = EvaluationCase(
        case_id="redaction-recheck-" + stage.value, group="privacy",
        stage=stage, request=request,
        expected=Expected(
            evaluation_status="complete", recommended_action=Action.REDACT,
            effective_action=Action.REDACT,
            checker_statuses={"pii": "flagged"},
            finding_rule_ids=("pii.email_address",),
            redaction_status="ready",
        ),
    )
    suite = Suite(format_version="1", suite_id="synthetic-recheck", cases=(case,))
    return engine, suite, request


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("code", ["pii_scan_deadline", "checker_timeout"])
def test_real_engine_failed_second_pass_has_private_free_cause_and_withholds_content(stage, code):
    engine, suite, request = _engine_and_suite(stage, recheck_code=code)
    actual = asyncio.run(engine.check(request, stage))
    assert actual.checker_results[0].status == "flagged"
    assert actual.evaluation_status == "incomplete"
    assert actual.recommended_action == actual.effective_action == Action.BLOCK
    assert actual.redaction.status == "withheld"
    assert actual.redaction.content is None
    assert len(actual.redaction.validation_results) == 1
    assert actual.redaction.validation_results[0].error_code == code

    report = asyncio.run(evaluate(suite, engine))
    row = report["cases"][0]
    assert row["matched"] is False
    assert row["evaluation_status"] == "incomplete"
    assert row["redaction_status"] == "withheld"
    assert row["checker_statuses"] == {"pii": "flagged"}
    assert row["checker_diagnostics"] == {}
    assert row["recheck_diagnostics"]["pii"]["error_code"] == code
    assert row["recheck_diagnostics"]["pii"]["latency_ms"] >= 0
    assert report["summary"]["mismatched"] == 1
    assert Report.model_validate(report)
    assert PRIVATE not in json.dumps(report)
    assert REPLACEMENT not in json.dumps(report)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_real_engine_successful_recheck_never_invents_failure_diagnostic(stage):
    engine, suite, request = _engine_and_suite(stage, recheck_code=None)
    result = asyncio.run(engine.check(request, stage))
    assert result.redaction.status == "ready"
    assert result.redaction.content == REPLACEMENT
    assert result.evaluation_status == "complete"

    report = asyncio.run(evaluate(suite, engine))
    row = report["cases"][0]
    assert row["matched"] is True
    assert row["recheck_diagnostics"] == {}
    assert row["checker_diagnostics"] == {}
    assert PRIVATE not in json.dumps(report)
    assert REPLACEMENT not in json.dumps(report)


def test_legacy_report_without_recheck_diagnostics_remains_comparable():
    engine, suite, _ = _engine_and_suite(Stage.INPUT, recheck_code="pii_scan_deadline")
    report = asyncio.run(evaluate(suite, engine))
    old = deepcopy(report)
    old["cases"][0].pop("recheck_diagnostics")
    assert Report.model_validate(old).cases[0].recheck_diagnostics == {}
    assert compare_reports(old, report)["summary"]["unchanged"] == 1


def test_recheck_metadata_rejects_clean_status_unselected_checker_and_raw_errors():
    engine, suite, _ = _engine_and_suite(Stage.INPUT, recheck_code="pii_scan_deadline")
    original = asyncio.run(evaluate(suite, engine))
    for change in (
        {"redaction_status": "ready"},
        {"recheck_diagnostics": {"invented": {"error_code": "pii_scan_deadline", "latency_ms": 1.0}}},
        {"recheck_diagnostics": {"pii": {"error_code": "SECRET_SOURCE_TEXT", "latency_ms": 1.0}}},
    ):
        malformed = deepcopy(original)
        malformed["cases"][0].update(change)
        with pytest.raises(ValidationError):
            Report.model_validate(malformed)
    injected = CheckerResult(checker_id="pii", checker_version="test", status="error",
                             error_code="PRIVATE_RAW_EXCEPTION")
    assert safe_checker_diagnostics((injected,))["pii"]["error_code"] == "other_error"
