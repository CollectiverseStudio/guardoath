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


def test_audited_cases_have_existing_module_tests_and_explicit_eval_evidence():
    manifest = json.loads((ROOT / "docs/use-case-manifest.json").read_text(encoding="utf-8"))
    audited = [row for row in manifest["cases"] if row["status"] == "partial" or row.get("audit_classification") == "not_implemented"]
    assert len(audited) >= 7
    for row in audited:
        evidence = row["evidence"]
        assert evidence["modules"] and evidence["tests"]
        assert isinstance(evidence["eval_case_ids"], list)
        assert row["audit_note"]
        for module in evidence["modules"]:
            assert (ROOT / module).is_file(), (row["id"], module)
        for test in evidence["tests"]:
            filename, separator, function = test.partition("::")
            assert separator and function.startswith("test_")
            text = (ROOT / filename).read_text(encoding="utf-8")
            assert f"def {function}(" in text, (row["id"], test)
        for case in evidence["eval_case_ids"]:
            suite, separator, case_id = case.partition(":")
            assert separator and case_id
            data = json.loads((ROOT / "evals" / f"{suite}.json").read_text(encoding="utf-8"))
            assert case_id in {entry["case_id"] for entry in data["cases"]}, (row["id"], case)
