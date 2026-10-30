"""Evidence quality regressions for GuardOath's 200-use-case audit.

This checks catalog bookkeeping, not effectiveness or implementation of a
security control. It never upgrades a use-case status automatically.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audited():
    data = json.loads((ROOT / "docs/use-case-manifest.json").read_text(encoding="utf-8"))
    return [c for c in data["cases"] if c.get("evidence") is not None]


def test_audited_case_ids_are_unique_and_evidence_references_are_not_duplicated():
    rows = audited()
    assert len({c["id"] for c in rows}) == len(rows)
    for case in rows:
        evidence = case["evidence"]
        for field in ("modules", "tests", "eval_case_ids"):
            values = evidence[field]
            assert isinstance(values, list), (case["id"], field)
            assert len(values) == len(set(values)), (case["id"], field)
        assert case["audit_note"].strip(), case["id"]


def test_negative_audits_cannot_be_labeled_verified_or_supported():
    for case in audited():
        if case.get("audit_classification") == "not_implemented":
            assert case["status"] == "review_required", case["id"]
            assert case["evidence"]["modules"], case["id"]
            assert case["evidence"]["tests"], case["id"]


def test_partial_audits_have_positive_code_evidence_and_explicit_eval_list():
    for case in audited():
        if case["status"] == "partial":
            evidence = case["evidence"]
            assert evidence["modules"] and evidence["tests"], case["id"]
            assert isinstance(evidence["eval_case_ids"], list), case["id"]


def test_saved_eval_ids_are_existing_unique_case_references():
    for case in audited():
        for reference in case["evidence"]["eval_case_ids"]:
            suite, sep, case_id = reference.partition(":")
            assert sep and suite and case_id, (case["id"], reference)
            path = ROOT / "evals" / (suite + ".json")
            assert path.is_file(), (case["id"], reference)
            entries = json.loads(path.read_text(encoding="utf-8"))["cases"]
            ids = [item["case_id"] for item in entries]
            assert len(ids) == len(set(ids)), suite
            assert case_id in ids, (case["id"], reference)
