"""Contract tests for repeated real conversation privacy release acceptance."""
import asyncio
import json

import pytest

from tools.conversation_privacy_release import FIXTURES, REQUIRED, run_conversation_matrix


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
