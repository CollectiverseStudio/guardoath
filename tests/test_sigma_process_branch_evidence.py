"""C11: positive, benign, absent and malformed branch evidence for Sigma batch 001.

Synthetic events are GuardOath-authored; matches are not proof of malicious intent.
Original YAML/JSON detection definitions remain the source of execution behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from guardoath.sigma_rules import import_rule
from guardoath.sigma_rules.evidence_catalog import BATCH_NAMES
from guardoath.sigma_rules.events import evaluate_event
from guardoath.sigma_rules.routing import load_routing_table

ROOT = Path(__file__).resolve().parents[1] / "guardoath" / "sigma_rules"
BATCH = json.loads((ROOT / "upstream_batch_001.json").read_text(encoding="utf-8"))
EVIDENCE = json.loads((ROOT / "process_branch_evidence.json").read_text(encoding="utf-8"))
RULES = {rule["id"]: rule for rule in BATCH["rules"]}
CASES = EVIDENCE["cases"]
ALL_BATCHES = [json.loads((ROOT / name).read_text(encoding="utf-8")) for name in BATCH_NAMES]
ALL_RULES = tuple(import_rule(row, row["source_path"], source_revision=batch["source_revision"])
                  for batch in ALL_BATCHES for row in batch["rules"])
TABLE = load_routing_table()
PROCESS_IDS = {row['id'] for batch in ALL_BATCHES for row in batch['rules']
               if row['logsource']['category'] == 'process_creation'}


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
    assert len(ALL_RULES) == 228
    for case in CASES:
        assert case["expected_positive_rule_ids"] == [case["rule_id"]]
        assert case["expected_negative_rule_ids"] == []


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
    for event_key, expected_key in (("positive_event", "expected_positive_rule_ids"),
                                    ("negative_event", "expected_negative_rule_ids")):
        findings = TABLE.evaluate(case[event_key],
            trusted_logsource={'product':'windows','category':'process_creation'},allowed_rule_ids=TABLE.rule_ids)
        assert {finding["rule_id"] for finding in findings} == set(case[expected_key])
        assert all(finding == next(rule.finding() for rule in ALL_RULES
                                   if rule.rule_id == finding["rule_id"]) for finding in findings)


def test_branch_inventory_covers_expected_alternates():
    assert {c["case_id"] for c in CASES} == {
        "7zip_description", "7zip_7zr_image", "7zip_7za_image",
        "7zip_original_filename", "7zip_update_action",
        "acccheck_original_name", "acccheck_process", "acccheck_window",
        "auditpol_original_name", "auditpol_disable", "auditpol_remove",
        "auditpol_restore",
    }


def test_all_153_rules_have_trusted_route_previews():
    from guardoath.sigma_rules.routing import RoutingTable
    batches = [json.loads((ROOT / f"upstream_batch_{n:03d}.json").read_text(encoding="utf-8"))
               for n in range(1, 11)]
    table = RoutingTable(batches)
    ids = table.rule_ids
    assert len(ids) == 228 and len(set(ids)) == 228
    plan = table.plan(
        trusted_logsource={"product": "windows", "category": "process_creation"},
        allowed_rule_ids=ids,
    )
    assert set(plan.selected_rule_ids) == PROCESS_IDS
    assert len(PROCESS_IDS) == 103 and set(RULES).issubset(PROCESS_IDS)
    assert len(plan.excluded_rule_ids) == 125
    assert plan.as_dict()["evaluation_performed"] is False


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_source_routed_complete_153_rule_inventory_overlap(case):
    from guardoath.sigma_rules.routing import RoutingTable
    batches = [json.loads((ROOT / f"upstream_batch_{n:03d}.json").read_text(encoding="utf-8"))
               for n in range(1, 11)]
    table = RoutingTable(batches)
    source = {"product": "windows", "category": "process_creation"}
    selected = table.plan(trusted_logsource=source, allowed_rule_ids=table.rule_ids)
    assert set(selected.selected_rule_ids) == PROCESS_IDS
    positive = table.evaluate(case["positive_event"], trusted_logsource=source,
                              allowed_rule_ids=table.rule_ids)
    negative = table.evaluate(case["negative_event"], trusted_logsource=source,
                              allowed_rule_ids=table.rule_ids)
    assert {finding["rule_id"] for finding in positive} == set(case["expected_positive_rule_ids"])
    assert {finding["rule_id"] for finding in negative} == set(case["expected_negative_rule_ids"])
    # Do not discard other genuine matches or treat the preview as authorization.
    assert all(finding["rule_id"] in selected.selected_rule_ids for finding in positive + negative)
    table = load_routing_table()
    for key in ("positive_event", "negative_event"):
        assert table.evaluate(case[key], trusted_logsource={"product":"windows", "category":"ps_script"},
                              allowed_rule_ids=table.rule_ids) == []
