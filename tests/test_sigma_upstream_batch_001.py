"""Provenance and behavioral fixtures for first three imported SigmaHQ rules."""
import json
from pathlib import Path
import pytest
from guardoath.sigma_rules import import_rule

PATH = Path(__file__).resolve().parents[1] / "guardoath" / "sigma_rules" / "upstream_batch_001.json"
DATA = json.loads(PATH.read_text(encoding="utf-8"))
RULES = DATA["rules"]

def test_upstream_batch_inventory():
    assert len(RULES) == 3
    assert len({rule["id"] for rule in RULES}) == 3
    assert DATA["license"] == "Detection Rule License 1.1 (DRL 1.1)"

@pytest.mark.parametrize("record", RULES, ids=lambda x: x["id"])
def test_upstream_sigma_positive_and_benign(record):
    imported = import_rule(record, record["source_path"])
    assert imported.matches(record["positive_event"])
    assert not imported.matches(record["negative_event"])
    assert imported.rule_id == record["id"]
    assert imported.title == record["title"]
    assert imported.author == record["author"]
    assert imported.source_url.endswith(record["source_path"])
    assert imported.license == DATA["license"]

@pytest.mark.parametrize("record", RULES, ids=lambda x: x["id"])
def test_upstream_sigma_missing_fields_do_not_match(record):
    imported = import_rule(record, record["source_path"])
    assert not imported.matches({})
