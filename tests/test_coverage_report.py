"""Validate use-case reporting and require evidence for verified statuses."""
import copy
import json
from pathlib import Path

import pytest

from valinator.coverage_report import summarize

ROOT = Path(__file__).resolve().parents[1]


def manifest():
    return json.loads((ROOT / "docs/use-case-manifest.json").read_text())


def test_200_case_report_is_not_a_completion_claim():
    result = summarize(manifest())
    assert result["total"] == 200
    assert result["verified"] == 0
    assert result["unverified"] == 200


def test_verified_requires_evidence():
    data = manifest()
    data["cases"][0]["status"] = "verified"
    with pytest.raises(ValueError, match="Verification evidence"):
        summarize(data)
    data["cases"][0]["verification_evidence"] = {
        "commit": "reviewed-commit-id", "tests": ["tests/test_specific_use_case.py"]
    }
    assert summarize(data)["verified"] == 1


def test_duplicate_or_missing_id_rejected():
    data = manifest()
    data["cases"][1]["id"] = "UC001"
    with pytest.raises(ValueError, match="200 unique"):
        summarize(data)


def test_unknown_status_rejected():
    data = manifest()
    data["cases"][0]["status"] = "passed"
    with pytest.raises(ValueError, match="Unknown status"):
        summarize(data)
