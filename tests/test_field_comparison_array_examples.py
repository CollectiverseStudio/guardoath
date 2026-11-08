"""FC-001–030: customer-visible rule examples must be executable JSON, including arrays.

The same tenant-owned sample metadata displayed in the authenticated dashboard
is evaluated through the real shared Engine, not by a parallel sample scorer.
"""
import asyncio
import json

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Stage
from guardoath.record_rules import record_rules
from guardoath.tenant_record import TenantRecord


def tenant(comparisons):
    return TenantRecord(
        tenant_id="array-examples", display_name="Synthetic array examples",
        mode="enforce",
        input_check_cross_field="flag", output_check_cross_field="block",
        comparisons=comparisons,
    )


def comparison(rule_id, left, right, operator="le", value_type="number"):
    return {"rule_id": rule_id, "left": left, "right": right,
            "operator": operator, "value_type": value_type,
            "stages": ["input", "output"]}


def evaluate(record, text, stage):
    req = CheckRequest(application_id=record.tenant_id, policy_version="tenant",
                       content=text, content_format="json")
    return asyncio.run(Engine([record.to_policy()], default_checkers()).check(req, stage))


@pytest.mark.parametrize("stage,action", [(Stage.INPUT, "flag"), (Stage.OUTPUT, "block")])
def test_real_array_example_and_lookalike_run_through_saved_tenant_engine(stage, action):
    rec = tenant([
        comparison("first_price", "/order/items/0/price", "/order/limit"),
        comparison("second_price", "/order/items/1/price", "/order/limit"),
    ])
    rows = record_rules(rec)
    for rule_id in ("first_price", "second_price"):
        entry = rows["business." + rule_id]
        assert entry["example_unavailable"] is None
        assert entry["look_alike_unavailable"] is None
        bad = json.loads(entry["example"])
        clean = json.loads(entry["look_alike"])
        for payload in (bad, clean):
            assert isinstance(payload["order"]["items"], list)
            assert len(payload["order"]["items"]) == 2
            assert all(isinstance(item["price"], int) for item in payload["order"]["items"])
        result = evaluate(rec, entry["example"], stage)
        assert result.evaluation_status == "complete"
        assert {f.rule_id for f in result.findings} == {"business." + rule_id}
        assert result.effective_action == action
        passed = evaluate(rec, entry["look_alike"], stage)
        assert passed.evaluation_status == "complete"
        assert passed.findings == ()
        assert passed.effective_action == "allow"


def test_mixed_numeric_and_named_siblings_keep_object_keys_and_all_samples_work():
    rec = tenant([
        comparison("numeric_key", "/items/0/price", "/max"),
        comparison("named_key", "/items/current/price", "/max"),
    ])
    for entry in record_rules(rec).values():
        for sample in (entry["example"], entry["look_alike"]):
            document = json.loads(sample)
            assert isinstance(document["items"], dict), "Mixed siblings must not force JSON arrays"
            assert set(document["items"]) == {"0", "current"}
            assert evaluate(rec, sample, Stage.INPUT).evaluation_status == "complete"
        assert {f.rule_id for f in evaluate(rec, entry["example"], Stage.INPUT).findings} == {entry["rule_id"]}
        assert evaluate(rec, entry["look_alike"], Stage.INPUT).findings == ()


@pytest.mark.parametrize("pointer", ["/rows/0/a~1b", "/rows/1/amount"])
def test_array_pointer_escaping_and_sparse_array_index_are_valid_examples(pointer):
    rec = tenant([comparison("array_value", pointer, "/cap")])
    entry = record_rules(rec)["business.array_value"]
    assert entry["example_unavailable"] is None and entry["look_alike_unavailable"] is None
    for sample in (entry["example"], entry["look_alike"]):
        payload = json.loads(sample)
        assert isinstance(payload["rows"], list)
        assert len(payload["rows"]) == (1 if "/0/" in pointer else 2)
        assert evaluate(rec, sample, Stage.INPUT).evaluation_status == "complete"
    assert {f.rule_id for f in evaluate(rec, entry["example"], Stage.INPUT).findings} == {"business.array_value"}
    assert evaluate(rec, entry["look_alike"], Stage.INPUT).findings == ()


def test_out_of_bounds_index_does_not_generate_unbounded_json_array():
    rec = tenant([comparison("large_numeric_key", "/items/1000000000/price", "/cap")])
    entry = record_rules(rec)["business.large_numeric_key"]
    assert entry["example_unavailable"] is None
    assert isinstance(json.loads(entry["example"])["items"], dict)
    assert evaluate(rec, entry["example"], Stage.INPUT).findings[0].rule_id == "business.large_numeric_key"
