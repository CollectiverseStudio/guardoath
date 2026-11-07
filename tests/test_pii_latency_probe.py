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
        "failed_contracts": 0,
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
    None, -1, float("nan"), float("inf"), float("-inf"), True, 500.01,
])
def test_missing_or_nonfinite_first_pass_timings_cannot_pass(invalid_latency):
    sample = _response()
    sample.checker_results = (_check(latency_ms=invalid_latency),)
    report = probe.summarize("dob-detection", "input", [sample])
    assert report["failed_contracts"] == 1
    assert report["first_pass"]["observed"] == 0


@pytest.mark.parametrize("invalid_latency", [
    None, -1, float("nan"), float("inf"), float("-inf"), True, 500.01,
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
