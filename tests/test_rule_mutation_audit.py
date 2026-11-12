"""The rule-drift generator must cover the entire static catalog reproducibly."""
import asyncio
import json

import pytest

from guardoath.rule_catalog import RULES
from tools.rule_mutation_audit import MAX_VARIANTS, WRAPPERS, audit, mutation


def test_catalog_includes_every_static_rule_and_up_to_269312_cases():
    assert len(RULES) == 526
    assert len(RULES) * 2 * 2 * MAX_VARIANTS == 269312
    assert len(WRAPPERS) == 8


def test_text_mutations_are_deterministic_and_bounded():
    for index in range(24):
        name, value = mutation("Example", "text", index)
        assert name and value.count("Example") == 1
        assert mutation("Example", "text", index) == (name, value)
        assert len(value) < 512


def test_json_reserialization_preserves_values_and_type():
    original = '{"note":"line\\nnext","flag":true,"id":123,"list":[1,2]}'
    for index in range(16):
        name, encoded = mutation(original, "json", index)
        assert name
        assert json.loads(encoded) == json.loads(original)


@pytest.mark.parametrize("value", [0, -1, 129, True, "8", 1.5])
def test_invalid_variant_count_rejected(value):
    with pytest.raises(ValueError):
        asyncio.run(audit(variants=value, limit=1))


def test_real_checker_small_end_to_end_drift_evidence_is_private():
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    result = asyncio.run(audit(variants=2, limit=3, both_stages=True))
    assert result["catalog_rules"] == 3
    assert result["expected_evaluations"] == result["totals"]["total"] == 24
    assert set(result["stages"]) == {"input", "output"}
    assert all(set(item) <= {"rule_id", "stage", "variant", "incomplete"}
               for item in result["misses"])
    assert all(set(item) <= {"rule_id", "stage", "variant"}
               for item in result["false_positives"])
    assert "Example" not in json.dumps(result)


def test_intentionally_invalid_json_examples_remain_invalid_without_aborting_catalog():
    for index in range(16):
        name, altered = mutation('{"status": ', "json", index)
        assert name.startswith("malformed_json_")
        with pytest.raises(json.JSONDecodeError):
            json.loads(altered)
