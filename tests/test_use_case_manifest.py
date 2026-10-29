"""Catalog completeness is not evidence of implementation or detector accuracy."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_exactly_200_unique_stable_use_case_ids():
    manifest = json.loads((ROOT / "docs/use-case-manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "1"
    cases = manifest["cases"]
    assert len(cases) == 200
    assert [row["id"] for row in cases] == [f"UC{i:03d}" for i in range(1, 201)]
    assert all(row["title"] and row["source"] and row["existing_coverage_note"] for row in cases)
    assert all(row["status"] in {"proposed", "review_required", "partial", "implemented_unverified", "verified"} for row in cases)
    assert all((ROOT / row["source"]).is_file() for row in cases)


def test_no_unverified_use_case_is_reported_as_verified():
    manifest = json.loads((ROOT / "docs/use-case-manifest.json").read_text(encoding="utf-8"))
    assert all(row["status"] != "verified" for row in manifest["cases"])
