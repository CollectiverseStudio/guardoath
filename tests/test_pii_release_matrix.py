"""Release matrix is source-backed, cross-suite and privacy-safe."""
import json

import pytest

from tools.pii_release_matrix import SUITES, run_matrix


def test_every_previous_tail_failure_and_both_stages_are_selected():
    ids = {case for _, _, _, group in SUITES for case in group}
    assert {
        "phone_number1.input", "phone_number1.output",
        "date_of_birth1.input", "date_of_birth1.output",
        "redact.enforce.text.input", "redact.enforce.text.output",
        "apac.kr-rrn.input", "apac.kr-rrn.output",
    } == ids
    assert len(SUITES) == 2


def test_release_matrix_runs_actual_local_privacy_engines_once():
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    import asyncio
    result = asyncio.run(run_matrix(1))
    assert result["accepted"], json.dumps(result, sort_keys=True)
    assert result["rounds"] == 1 and result["shipped_checker_deadline_ms"] == 500
    assert sum(len(g["cases"]) for g in result["groups"]) == 8
    assert all(c["samples"] == 1 and c["incomplete"] == 0 and c["pii_errors"] == 0
               for g in result["groups"] for c in g["cases"])
    assert "123-45-6789" not in json.dumps(result)


@pytest.mark.parametrize("invalid", [0, -1, 101, 1.5, True])
def test_invalid_round_counts_refused(invalid):
    with pytest.raises(ValueError):
        import asyncio
        asyncio.run(run_matrix(invalid))
