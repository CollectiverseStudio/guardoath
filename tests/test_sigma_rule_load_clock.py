"""Loading a Sigma rule checks its structure only and never depends on the clock (2026-11-04).

Local test run 2026-11-04 (full suite, 625 s): test_each_missing_required_asset_fails_despite_checkout_having_it got
"invalid Sigma evidence metadata" instead of "missing" for one asset, and test_malformed_field_values_do_not_match hit
"Sigma evaluation deadline exceeded" for a rule with no regular expression. Loading a rule evaluated its condition
against an empty event under the 1-second evaluation limit, so a pause of the machine refused a valid rule. Loading now
checks structure only (matcher.validate_detection); evaluating an event keeps the limit. Tests that only check what a
rule matches run with the Sigma clock frozen (tests/conftest.py).
"""
import json
from pathlib import Path

import pytest

from guardoath.sigma_rules import SigmaBudgetExceeded, UnsupportedSigmaRule, budgets, import_rule
from guardoath.sigma_rules.evidence_catalog import BATCH_NAMES, build_evidence_catalog
from guardoath.sigma_rules.matcher import match_detection, match_selection, validate_detection

ROOT = Path(__file__).resolve().parents[1] / "guardoath" / "sigma_rules"
BATCHES = [json.loads((ROOT / name).read_text(encoding="utf-8")) for name in BATCH_NAMES]
RECORDS = [(batch.get("source_revision"), record) for batch in BATCHES for record in batch["rules"]]


def expired_clock(monkeypatch):
    """Every reading is 10 seconds after the last one, so every time limit has expired by the next reading."""
    clock = [0.0]

    def tick():
        clock[0] += 10.0
        return clock[0]
    monkeypatch.setattr(budgets, "monotonic", tick)


def test_every_shipped_rule_loads_while_every_time_limit_has_expired(monkeypatch):
    expired_clock(monkeypatch)
    rules = [import_rule(record, record["source_path"], source_revision=revision) for revision, record in RECORDS]
    assert len(rules) == len(RECORDS) == len({rule.rule_id for rule in rules}) > 0


def test_the_evidence_catalog_builds_while_every_time_limit_has_expired(monkeypatch):
    expired_clock(monkeypatch)
    assert len(build_evidence_catalog(BATCHES)) == len(RECORDS)


def test_evaluating_an_event_still_has_the_time_limit(monkeypatch):
    revision, record = RECORDS[0]
    rule = import_rule(record, record["source_path"], source_revision=revision)
    expired_clock(monkeypatch)
    with pytest.raises(SigmaBudgetExceeded, match="deadline"):
        rule.matches({"CommandLine": "x", "ScriptBlockText": "x"})


def test_the_structure_check_accepts_every_shipped_rule_and_empty_events_match_no_selection():
    for _, record in RECORDS:
        assert validate_detection(record["detection"]) is None
        # The old check evaluated an empty event. Every selection is False on an empty event, so deciding each
        # selection as False (the structure check) walks the same condition and can raise only the same errors.
        for name, selection in record["detection"].items():
            if name != "condition":
                assert match_selection(selection, {}) is False, (record["id"], name)


@pytest.mark.parametrize("detection", [
    {"selection": {"CommandLine|contains": "a"}, "condition": "other"},
    {"selection": {"CommandLine|contains": "a"}, "condition": "1 of filter_*"},
    {"selection": {"CommandLine|contains": "a"}, "condition": "(selection"},
    {"selection": {"CommandLine|contains": "a"}, "condition": "selection)"},
    {"selection": {"CommandLine|contains": "a"}, "condition": "selection selection"},
    {"selection": {"CommandLine|contains": "a"}, "condition": "all selection"},
    {"selection": {"CommandLine|contains": "a"}, "condition": "selection and"},
    {"selection": {"CommandLine|contains": "a"}, "condition": "selection | count() > 1"},
    {"selection": {"CommandLine|contains": "a"}, "condition": ""},
    {"selection": {"CommandLine|contains": "a"}, "condition": 1},
    {"selection": {"CommandLine|contains": "a"}},
    {"selection": {"CommandLine|contains": "a"}, "timeframe": "5m", "condition": "selection"},
    {"selection": {"CommandLine|unsupported": "a"}, "condition": "selection"},
    {"selection": [], "condition": "selection"},
    {"": {"CommandLine|contains": "a"}, "condition": "selection"},
    {"selection": {"CommandLine|contains": "a"}, "condition": "not " * 20 + "selection"},
    [], "selection",
])
def test_the_structure_check_refuses_exactly_what_loading_refused_before(detection):
    with pytest.raises(UnsupportedSigmaRule) as now:
        validate_detection(detection)
    with pytest.raises(UnsupportedSigmaRule) as before:
        match_detection(detection, {})
    assert str(now.value) == str(before.value)
