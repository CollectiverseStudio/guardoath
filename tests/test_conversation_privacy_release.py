"""Contract tests for repeated real conversation privacy release acceptance."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from tools.conversation_privacy_release import (FIXTURES, REQUIRED, run_conversation_matrix,
    safe_failure_label, safe_primary_failure_receipts, case_matches_shipped_profile)


def test_conversation_matrix_preserves_all_prior_windows_cases_and_checkers():
    assert len(FIXTURES) == 4
    assert {name for name, _, _ in FIXTURES} == {"benign", "email", "secret", "unicode"}
    assert REQUIRED == {"pii", "secrets", "text_security", "encoded_pii"}


@pytest.mark.parametrize("invalid", [0, -1, 101, 1.5, True])
def test_invalid_conversation_repeat_count_fails(invalid):
    with pytest.raises(ValueError):
        asyncio.run(run_conversation_matrix(invalid))


def test_real_shipped_conversation_profile_both_stages_once():
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    result = asyncio.run(run_conversation_matrix(1))
    assert result["accepted"], json.dumps(result, sort_keys=True)
    assert len(result["cases"]) == 8
    assert {c["case_id"] for c in result["cases"]} == {
        stage + "." + name for stage in ("input", "output")
        for name, _, _ in FIXTURES
    }
    assert all(c["samples"] == 1 and c["mismatches"] == 0
               and c["checker_failures"] == {} for c in result["cases"])
    assert result["shipped_checker_deadline_ms"] == 500
    serialized = json.dumps(result)
    assert "jane@example.com" not in serialized
    assert "synthetic-example!42" not in serialized


@pytest.mark.parametrize("checker,status,code,expected", [
    ("pii", "error", "checker_timeout", "pii:checker_timeout"),
    ("secrets", "error", "pii_capacity_exceeded", "secrets:pii_capacity_exceeded"),
    ("pii", "error", "SECRET_SOURCE_PAYLOAD", "pii:other_error"),
    ("PRIVATE_CHECKER_ID", "error", "SECRET_SOURCE_PAYLOAD", "other_checker:other_error"),
    ("text_security", "unsupported", "SECRET_SOURCE_PAYLOAD", "text_security:unsupported"),
    ("encoded_pii", "skipped", None, "encoded_pii:skipped"),
    ("pii", "error", None, "pii:other_error"),
    ("pii", "flagged", None, "pii:flagged"),
])
def test_privacy_failure_receipts_never_copy_raw_checker_metadata(checker, status, code, expected):
    label = safe_failure_label(checker, status, code)
    assert label == expected
    assert "SECRET_SOURCE_PAYLOAD" not in label
    assert "PRIVATE_CHECKER_ID" not in label


def _synthetic_receipts(statuses, action="flag", *, incomplete=False, simulated=False):
    return SimpleNamespace(
        evaluation_status="incomplete" if incomplete else "complete",
        recommended_action=SimpleNamespace(value=action),
        checker_results=tuple(SimpleNamespace(
            checker_id=checker, status=status, simulated=simulated,
            error_code="checker_timeout" if status == "error" else None,
            latency_ms=17.25,
        ) for checker, status in statuses.items()),
    )


def test_shipped_conversation_gate_rejects_unexpected_extra_finding():
    statuses = dict.fromkeys(REQUIRED, "passed")
    statuses["pii"] = "flagged"
    stage = SimpleNamespace(value="input")
    assert case_matches_shipped_profile(_synthetic_receipts(statuses), stage, "pii")
    statuses["encoded_pii"] = "flagged"
    surprise = _synthetic_receipts(statuses)
    assert not case_matches_shipped_profile(surprise, stage, "pii")
    assert safe_primary_failure_receipts(surprise.checker_results, "pii") == (
        "encoded_pii:flagged@17.2ms",
    )


def test_shipped_conversation_gate_rejects_missing_error_and_simulated_checker():
    statuses = dict.fromkeys(REQUIRED, "passed")
    statuses["pii"] = "flagged"
    stage = SimpleNamespace(value="input")
    assert not case_matches_shipped_profile(_synthetic_receipts(statuses, simulated=True), stage, "pii")
    assert not case_matches_shipped_profile(_synthetic_receipts(statuses, incomplete=True), stage, "pii")
    assert not case_matches_shipped_profile(
        _synthetic_receipts({k: v for k, v in statuses.items() if k != "pii"}), stage, "pii"
    )
    statuses["pii"] = "error"
    failure = _synthetic_receipts(statuses)
    assert not case_matches_shipped_profile(failure, stage, "pii")
    assert safe_primary_failure_receipts(failure.checker_results, "pii") == (
        "pii:checker_timeout@17.2ms",
    )


def test_primary_failure_receipt_redacts_untrusted_error_and_latency():
    untrusted = SimpleNamespace(checker_id="PRIVATE_CHECKER_ID", status="error",
                                error_code="PRIVATE_EXCEPTION_TEXT", latency_ms=float("nan"))
    labels = safe_primary_failure_receipts((untrusted,), None)
    assert labels == ("other_checker:other_error@0.0ms",)
    assert "PRIVATE" not in json.dumps(labels)
