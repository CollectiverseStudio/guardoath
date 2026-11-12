"""Privacy-safe attribution of PII worker deadline versus outer Engine timeout.

The two failures have identical fail-closed enforcement but require different
operational fixes. Diagnostic data never contains source/exception contents.
"""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

import guardoath.pii as pii
from guardoath.compare import Report, compare_reports
from guardoath.evaluate import EvaluationCase, Expected, Suite, evaluate, safe_checker_diagnostics
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage


def _privacy_check(monkeypatch, scanner):
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    from guardoath.checkers import default_checkers
    from guardoath.engine import Engine

    monkeypatch.setattr(pii, "_bounded_local_scan", scanner)
    policy = Policy(
        application_id="synthetic", version="v1", timeout_ms=60,
        mode="enforce", failure_action="block",
        rules=(Rule(checker_id="pii", on_finding=Action.REDACT),),
    )
    request = CheckRequest(
        application_id="synthetic", policy_version="v1",
        content="synthetic.person@example.com", content_format="text",
    )
    return asyncio.run(Engine([policy], default_checkers(enable_pii=True)).check(request, Stage.INPUT))


def test_worker_local_deadline_is_classified_without_leaking_exception(monkeypatch):
    async def inner_timeout(*_args):
        raise TimeoutError("PRIVATE_SYNTHETIC_TIMEOUT_DETAIL")

    result = _privacy_check(monkeypatch, inner_timeout)
    pii_result = result.checker_results[0]
    assert pii_result.status == "error"
    assert pii_result.error_code == "pii_scan_deadline"
    assert result.evaluation_status == "incomplete"
    assert result.recommended_action == result.effective_action == Action.BLOCK
    assert result.redaction.status == "withheld"
    assert result.redaction.content is None
    assert "PRIVATE_SYNTHETIC_TIMEOUT_DETAIL" not in result.model_dump_json()
    assert "synthetic.person@example.com" not in result.model_dump_json()


def test_outer_wait_for_timeout_remains_separate_and_fail_closed(monkeypatch):
    async def pending(*_args):
        await asyncio.Event().wait()

    result = _privacy_check(monkeypatch, pending)
    pii_result = result.checker_results[0]
    assert pii_result.status == "error"
    assert pii_result.error_code == "checker_timeout"
    assert result.evaluation_status == "incomplete"
    assert result.recommended_action == result.effective_action == Action.BLOCK
    assert result.redaction.status == "withheld"
    assert result.redaction.content is None


@pytest.mark.parametrize("untrusted", [
    "PRIVATE_SSN_123_45_6789", "adapter crash: access_key=SECRET",
    None, "not_registered",
])
def test_diagnostics_never_copy_untrusted_error_text(untrusted):
    cases = [
        SimpleNamespace(checker_id="pii", status="error", error_code=untrusted, latency_ms=11.2576),
        SimpleNamespace(checker_id="secrets", status="passed", error_code="SHOULD_NOT_APPEAR", latency_ms=5),
    ]
    assert safe_checker_diagnostics(cases) == {
        "pii": {"error_code": "other_error", "latency_ms": 11.258}
    }


def test_source_free_evaluation_report_supports_legacy_baselines():
    private = "PRIVATE_SYNTHETIC_PROMPT_NEVER_PUBLISH"
    request = CheckRequest(application_id="synthetic", policy_version="v1",
                           content=private, content_format="text")
    case = EvaluationCase(
        case_id="synthetic-deadline", group="privacy", stage=Stage.INPUT, request=request,
        expected=Expected(
            evaluation_status="complete",
            recommended_action=Action.ALLOW,
            effective_action=Action.ALLOW,
            checker_statuses={"pii": "passed"},
        ),
    )
    class EngineStub:
        def has_policy(self, application_id, version):
            return application_id == "synthetic" and version == "v1"

        async def check(self, request, stage):
            return SimpleNamespace(
                evaluation_status="incomplete",
                recommended_action=Action.BLOCK,
                effective_action=Action.BLOCK,
                checker_results=(SimpleNamespace(
                    checker_id="pii", status="error",
                    error_code="pii_scan_deadline", latency_ms=56.1239,
                ),),
                findings=(), redaction=SimpleNamespace(status="withheld", validation_results=()),
                simulated=False, total_latency_ms=57.357,
            )

    report = asyncio.run(evaluate(
        Suite(format_version="1", suite_id="synthetic-deadline", cases=(case,)), EngineStub(),
    ))
    assert report["summary"]["mismatched"] == 1
    assert report["cases"][0]["checker_diagnostics"] == {
        "pii": {"error_code": "pii_scan_deadline", "latency_ms": 56.124}
    }
    assert private not in json.dumps(report)
    assert Report.model_validate(report).cases[0].checker_diagnostics["pii"].error_code == "pii_scan_deadline"

    # Old persisted report JSON lacks this additive field and remains readable.
    old_report = deepcopy(report)
    old_report["cases"][0].pop("checker_diagnostics")
    assert Report.model_validate(old_report).cases[0].checker_diagnostics == {}
    assert compare_reports(old_report, report)["summary"]["unchanged"] == 1


def test_inconsistent_failure_metadata_is_rejected():
    report = {
        "case_id": "case", "group": "privacy", "stage": "input",
        "matched": False, "differences": ["checker_statuses"],
        "evaluation_status": "incomplete", "recommended_action": "block",
        "effective_action": "block", "checker_statuses": {"pii": "passed"},
        "finding_rule_ids": [], "redaction_status": "not_requested",
        "simulated": False, "latency_ms": 5.0,
        "checker_diagnostics": {"pii": {"error_code": "pii_scan_deadline", "latency_ms": 1.0}},
    }
    from pydantic import ValidationError
    from guardoath.compare import ReportCase
    with pytest.raises(ValidationError):
        ReportCase.model_validate(report)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("last_operation", ["overlap_resolution", "redaction_assembly"])
def test_worker_deadline_after_final_detector_or_redaction_fails_closed(
    monkeypatch, stage, last_operation,
):
    """Late terminal detector/assembly work cannot become a clean or releasable verdict.

    Simulate a deadline crossing *after* the final recognizer/overlap resolution
    or after the redacted output has been assembled, with the worker still
    executing. No sleeps or environment-specific timing tolerances are used.
    The Engine must receive a primary pii_scan_deadline, not merely a later
    redaction-recheck failure or a false successful scan.
    """
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    from guardoath.checkers import default_checkers
    from guardoath.engine import Engine

    policy = Policy(
        application_id="synthetic", version="v1", timeout_ms=500,
        mode="enforce", failure_action="block",
        rules=(Rule(checker_id="pii", on_finding=Action.REDACT),),
    )
    engine = Engine([policy], default_checkers(enable_pii=True))
    expired = {"value": False}
    original_monotonic = pii.monotonic
    # Use a deterministic simulated deadline crossing on the PII worker only.
    # Engine wait_for and the real test runner continue to use actual clocks.
    start = original_monotonic()
    monkeypatch.setattr(pii, "monotonic",
                        lambda: start + (0.501 if expired["value"] else 0.0))

    if last_operation == "overlap_resolution":
        original = pii.resolve_overlaps

        def finish_after_overlaps(candidates, check_deadline=lambda: None):
            spans = original(candidates, check_deadline)
            expired["value"] = True
            return spans

        monkeypatch.setattr(pii, "resolve_overlaps", finish_after_overlaps)
    else:
        original = pii._assemble_redacted_text

        def finish_after_redaction(text, spans):
            result = original(text, spans)
            expired["value"] = True
            return result

        monkeypatch.setattr(pii, "_assemble_redacted_text", finish_after_redaction)

    private = "PRIVATE.jane@example.com"
    result = asyncio.run(engine.check(CheckRequest(
        application_id="synthetic", policy_version="v1",
        content=private, content_format="text",
    ), stage))

    assert expired["value"] is True
    primary = next(check for check in result.checker_results if check.checker_id == "pii")
    assert (primary.status, primary.error_code) == ("error", "pii_scan_deadline")
    assert result.evaluation_status == "incomplete"
    assert result.recommended_action == result.effective_action == Action.BLOCK
    assert result.redaction.content is None
    assert private not in result.model_dump_json()
