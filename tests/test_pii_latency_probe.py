"""Source-free, deterministic contracts for the local 500-ms PII probe."""
import json
from types import SimpleNamespace

import pytest

from tools import pii_latency_probe as probe


def _check(status="flagged", latency_ms=100, error_code=None):
    return SimpleNamespace(checker_id="pii", status=status, latency_ms=latency_ms,
                           error_code=error_code)


def _response(*, status="complete", first=100, recheck=80,
              redaction="preview", finding=True):
    return SimpleNamespace(
        evaluation_status=status,
        checker_results=(_check(latency_ms=first),),
        redaction=SimpleNamespace(
            status=redaction, content="synthetic safe replacement" if redaction == "preview" else None,
            validation_results=(_check(status="passed", latency_ms=recheck),) if recheck is not None else (),
        ),
        findings=(SimpleNamespace(rule_id="pii.date_of_birth"),) if finding else (),
    )


def test_summary_reports_only_fixed_case_stage_counts_and_numeric_timings():
    summary = probe.summarize("dob-redaction", "input",
                              [_response(first=90, recheck=70), _response(first=110, recheck=80)])
    assert summary == {
        "case": "dob-redaction", "stage": "input", "requests": 2,
        "failed_contracts": 0, "failure_causes": {},
        "first_pass": {"observed": 2, "median_ms": 100, "max_ms": 110},
        "redaction_recheck": {"observed": 2, "median_ms": 75, "max_ms": 80},
    }
    assert "synthetic safe replacement" not in json.dumps(summary)


@pytest.mark.parametrize("result", [
    _response(status="incomplete"),
    _response(first=501),
    _response(recheck=501),
    _response(redaction="withheld"),
    _response(recheck=None),
    _response(finding=False, redaction="withheld"),
])
def test_recheck_failure_or_over_deadline_is_not_reported_clean(result):
    assert probe.summarize("dob-redaction", "output", [result])["failed_contracts"] == 1


def test_detection_requires_finding_and_treats_errors_as_failures():
    assert probe.summarize("dob-detection", "input", [_response(finding=False)])["failed_contracts"] == 1
    assert probe.summarize("dob-detection", "input", [_response(finding=True)])["failed_contracts"] == 0


def test_cli_returns_nonzero_on_failed_report_and_never_exposes_messages(monkeypatch, capsys):
    async def fake_probe(rounds, concurrency):
        assert (rounds, concurrency) == (2, 1)
        return {"schema_version": 1, "all_contracts_passed": False,
                "results": [{"case": "dob-detection", "failed_contracts": 1}]}
    monkeypatch.setattr(probe, "probe", fake_probe)
    assert probe.main(["--rounds", "2", "--concurrency", "1"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["all_contracts_passed"] is False


def test_cli_error_suppresses_private_exception_and_traceback(monkeypatch, capsys):
    secret = "PRIVATE-CUSTOMER-VALUE-MUST-NEVER-ESCAPE"
    async def unavailable(_rounds, _concurrency):
        raise RuntimeError(secret)
    monkeypatch.setattr(probe, "probe", unavailable)
    assert probe.main(["--rounds", "1", "--concurrency", "1"]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["reason"] == "probe_unavailable"
    assert secret not in captured.out


def test_bounds_disallow_unbounded_load():
    with pytest.raises(SystemExit) as exc:
        probe.main(["--rounds", "101", "--concurrency", "4"])
    assert exc.value.code == 2


def test_missing_checker_or_missing_detection_never_counts_as_clean():
    blank = _response()
    blank.checker_results = ()
    assert probe.summarize("dob-detection", "input", [blank])["failed_contracts"] == 1
    without_finding = _response(finding=False)
    assert probe.summarize("dob-redaction", "output", [without_finding])["failed_contracts"] == 1


@pytest.mark.parametrize("invalid_latency", [
    None, -1, float("nan"), float("inf"), float("-inf"), True,
])
def test_missing_or_nonfinite_first_pass_timings_cannot_pass(invalid_latency):
    sample = _response()
    sample.checker_results = (_check(latency_ms=invalid_latency),)
    report = probe.summarize("dob-detection", "input", [sample])
    assert report["failed_contracts"] == 1
    assert report["first_pass"]["observed"] == 0


@pytest.mark.parametrize("invalid_latency", [
    None, -1, float("nan"), float("inf"), float("-inf"), True,
])
def test_missing_or_nonfinite_redaction_recheck_timings_cannot_pass(invalid_latency):
    sample = _response()
    sample.redaction.validation_results = (_check(status="passed", latency_ms=invalid_latency),)
    report = probe.summarize("dob-redaction", "output", [sample])
    assert report["failed_contracts"] == 1
    assert report["redaction_recheck"]["observed"] == 0


@pytest.mark.parametrize("bad_status", ["passed", "error", "disabled", "not_applicable"])
def test_expected_pii_finding_requires_flagged_checker_status(bad_status):
    sample = _response()
    sample.checker_results = (_check(status=bad_status),)
    assert probe.summarize("dob-detection", "input", [sample])["failed_contracts"] == 1


def test_wrong_checker_cannot_satisfy_privacy_gate():
    sample = _response()
    sample.checker_results = (SimpleNamespace(checker_id="secrets", status="flagged",
                                               latency_ms=30, error_code=None),)
    assert probe.summarize("dob-detection", "input", [sample])["failed_contracts"] == 1


@pytest.mark.parametrize("preview", [
    "DOB: 1980-02-29",
    "DOB: 1980-02-29 [REDACTED]",
])
def test_redaction_preview_must_remove_synthetic_dob(preview):
    sample = _response()
    sample.redaction.content = preview
    result = probe.summarize("dob-redaction", "input", [sample])
    assert result["failed_contracts"] == 1
    assert preview not in json.dumps(result)


def test_real_redaction_preview_with_private_value_removed_passes():
    sample = _response()
    sample.redaction.content = "DOB: [REDACTED]"
    result = probe.summarize("dob-redaction", "output", [sample])
    assert result["failed_contracts"] == 0
    assert result["redaction_recheck"]["observed"] == 1


@pytest.mark.parametrize("rechecks", [
    (_check(status="passed", latency_ms=50), _check(status="passed", latency_ms=60)),
    (SimpleNamespace(checker_id="secrets", status="passed", latency_ms=50, error_code=None),),
])
def test_redaction_recheck_requires_exactly_one_named_pii_checker(rechecks):
    sample = _response()
    sample.redaction.validation_results = rechecks
    result = probe.summarize("dob-redaction", "output", [sample])
    assert result["failed_contracts"] == 1


def test_release_probe_leak_witness_matches_its_actual_synthetic_case():
    fixture = next(content for case, _policy, content in probe._CASES
                   if case == "dob-redaction")
    assert probe._SYNTHETIC_REDACTION_DOB in fixture


@pytest.mark.parametrize("invalid_preview", [None, [], {"unexpected": "payload"}])
def test_redaction_preview_requires_text_not_an_unverifiable_container(invalid_preview):
    sample = _response()
    sample.redaction.content = invalid_preview
    report = probe.summarize("dob-redaction", "input", [sample])
    assert report["failed_contracts"] == 1


def test_first_pass_deadline_error_is_attributed_without_private_content():
    sample = _response(status="incomplete", first=594.031)
    sample.checker_results = (_check(status="error", latency_ms=594.031,
                                    error_code="pii_scan_deadline"),)
    report = probe.summarize("dob-detection", "input", [sample])
    assert report["failed_contracts"] == 1
    assert report["failure_causes"] == {
        "evaluation/incomplete": 1,
        "first_pass/latency_invalid_or_late": 1,
        "first_pass/pii_scan_deadline": 1,
    }
    assert report["first_pass"] == {"observed": 1, "median_ms": 594.031, "max_ms": 594.031}


def test_redaction_recheck_deadline_is_attributed_independently():
    sample = _response(recheck=611.02, redaction="withheld")
    sample.redaction.validation_results = (
        _check(status="error", latency_ms=611.02,
               error_code="checker_timeout"),)
    report = probe.summarize("dob-redaction", "output", [sample])
    assert report["failed_contracts"] == 1
    assert report["failure_causes"] == {
        "redaction_recheck/checker_timeout": 1,
        "redaction_recheck/latency_invalid_or_late": 1,
    }


def test_extra_passing_checker_is_not_counted_as_valid_pii_coverage():
    sample = _response()
    sample.checker_results += (_check(status="passed", latency_ms=10),)
    report = probe.summarize("dob-detection", "input", [sample])
    assert report["failed_contracts"] == 1
    assert report["failure_causes"] == {"first_pass/wrong_checker_count": 1}


def test_untrusted_error_code_and_private_text_never_enter_latency_report():
    private = "PRIVATE-REQUEST-VALUE-DO-NOT-REPORT"
    sample = _response(status="incomplete")
    sample.checker_results = (_check(status="error", latency_ms=50,
                                    error_code=private),)
    report = probe.summarize("dob-detection", "input", [sample])
    assert report["failed_contracts"] == 1
    assert report["failure_causes"]["first_pass/other_error"] == 1
    assert private not in json.dumps(report)


def test_failure_causes_count_once_per_response_and_not_per_duplicate_code():
    sample = _response(status="incomplete")
    sample.checker_results = (_check(status="error", latency_ms=501,
                                    error_code="checker_timeout"),)
    result = probe.summarize("dob-detection", "input", [sample, sample])
    assert result["failed_contracts"] == 2
    assert result["failure_causes"]["first_pass/checker_timeout"] == 2
    assert result["failure_causes"]["first_pass/latency_invalid_or_late"] == 2


@pytest.mark.parametrize("phase", ["first_pass", "redaction_recheck"])
@pytest.mark.parametrize("latency", [500.01, 594.031, 611.02])
def test_late_measurements_remain_visible_but_never_pass(phase, latency):
    sample = _response(**({"first": latency} if phase == "first_pass" else {"recheck": latency}))
    report = probe.summarize("dob-redaction", "input", [sample])
    assert report["failed_contracts"] == 1
    assert report["failure_causes"][phase + "/latency_invalid_or_late"] == 1
    assert report[phase] == {"observed": 1, "median_ms": latency, "max_ms": latency}


def test_mixed_timings_include_late_tail_without_hiding_failed_contracts():
    report = probe.summarize("dob-redaction", "output", [
        _response(first=100, recheck=80), _response(first=594, recheck=611),
    ])
    assert report["failed_contracts"] == 1
    assert report["first_pass"] == {"observed": 2, "median_ms": 347, "max_ms": 594}
    assert report["redaction_recheck"] == {"observed": 2, "median_ms": 345.5, "max_ms": 611}
