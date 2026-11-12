"""Contract tests for repeated real conversation privacy release acceptance."""
import asyncio
import json

import pytest

from tools.conversation_privacy_release import FIXTURES, REQUIRED, run_conversation_matrix, safe_failure_label


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
])
def test_privacy_failure_receipts_never_copy_raw_checker_metadata(checker, status, code, expected):
    label = safe_failure_label(checker, status, code)
    assert label == expected
    assert "SECRET_SOURCE_PAYLOAD" not in label
    assert "PRIVATE_CHECKER_ID" not in label
