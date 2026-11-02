"""C11: positive, benign, absent and malformed branch evidence for Sigma batch 001.

Synthetic events are GuardOath-authored; matches are not proof of malicious intent.
Original YAML/JSON detection definitions remain the source of execution behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from guardoath.sigma_rules import import_rule

ROOT = Path(__file__).resolve().parents[1] / "guardoath" / "sigma_rules"
BATCH = json.loads((ROOT / "upstream_batch_001.json").read_text(encoding="utf-8"))
EVIDENCE = json.loads((ROOT / "process_branch_evidence.json").read_text(encoding="utf-8"))
RULES = {rule["id"]: rule for rule in BATCH["rules"]}
CASES = EVIDENCE["cases"]


def imported(case):
    source = RULES[case["rule_id"]]
    return import_rule(source, source["source_path"], source_revision=BATCH["source_revision"])


def test_c11_inventory_and_immutable_source_references():
    assert EVIDENCE["schema_version"] == 1
    assert EVIDENCE["source_batch"] == "upstream_batch_001.json"
    assert EVIDENCE["source_revision"] == BATCH["source_revision"]
    assert len(CASES) == 12
    assert len({case["case_id"] for case in CASES}) == len(CASES)
    assert {case["rule_id"] for case in CASES} == set(RULES)
    assert all("GuardOath-authored" in case["origin"] for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_alternative_branch_positive_and_benign_control(case):
    rule = imported(case)
    assert rule.matches(case["positive_event"]) is True
    assert rule.matches(case["negative_event"]) is False


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_missing_every_essential_event_field_never_matches(case):
    rule = imported(case)
    for field in case["positive_event"]:
        malformed = {key: value for key, value in case["positive_event"].items() if key != field}
        assert rule.matches(malformed) is False, (case["case_id"], field)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
@pytest.mark.parametrize("replacement", [None, False, 0, {}, []], ids=["null", "false", "number", "object", "array"])
def test_malformed_event_field_fails_closed(case, replacement):
    rule = imported(case)
    for field in case["positive_event"]:
        malformed = {**case["positive_event"], field: replacement}
        assert rule.matches(malformed) is False, (case["case_id"], field, replacement)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_cross_rule_matches_are_observed_not_silently_suppressed(case):
    # A look-alike for one rule can legitimately trigger another upstream rule.
    # Record the entire match set rather than claiming global nonmalicious intent.
    imports = {rid: import_rule(row, row["source_path"], source_revision=BATCH["source_revision"])
               for rid, row in RULES.items()}
    positive_matches = {rid for rid, rule in imports.items() if rule.matches(case["positive_event"])}
    benign_matches = {rid for rid, rule in imports.items() if rule.matches(case["negative_event"])}
    assert case["rule_id"] in positive_matches
    assert case["rule_id"] not in benign_matches


def test_branch_inventory_covers_expected_alternates():
    assert {c["case_id"] for c in CASES} == {
        "7zip_description", "7zip_7zr_image", "7zip_7za_image",
        "7zip_original_filename", "7zip_update_action",
        "acccheck_original_name", "acccheck_process", "acccheck_window",
        "auditpol_original_name", "auditpol_disable", "auditpol_remove",
        "auditpol_restore",
    }
