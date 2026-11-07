"""Source-free, deterministic contracts for the local 500-ms PII probe."""
import json
from types import SimpleNamespace

import pytest

from tools import pii_latency_probe as probe


def _check(status="flagged", latency_ms=100, error_code=None):
    return SimpleNamespace(status=status, latency_ms=latency_ms,
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
