"""FC-031–050: advanced dashboard samples preserve array-index pointer semantics.

The same sample metadata used by the authenticated dashboard must be runnable
through the authoritative saved tenant's shared Engine, not a separate scorer.
"""
import asyncio
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.hosted import TenantService
from guardoath.models import (
    AdvancedArithmeticRule, AdvancedIdentityRule, AdvancedConversionTimeRule,
    CheckRequest, Stage,
)
from guardoath.record_rules import ARITHMETIC_EXAMPLES, record_rules
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore


def example_rule(family, kind, index=0):
    if family == "advanced_arithmetic":
        roles = ARITHMETIC_EXAMPLES[kind][0]
        model = AdvancedArithmeticRule
    elif family == "advanced_identity":
        roles = {
            "order_id_match": {"order_id": "", "shipment_order_id": ""},
            "serial_unique": {"serials": []},
        }[kind]
        model = AdvancedIdentityRule
    else:
        roles = {
            "unit_load_kg_cap": {"load": 0, "load_unit": "", "max_kg": 0},
            "cold_chain_excursion": {"excursion_start": "", "excursion_end": "", "allowed_minutes": 0},
        }[kind]
        model = AdvancedConversionTimeRule
    return model(rule_id="array_" + kind, kind=kind,
                 fields={role: f"/batch/{index}/{role}" for role in roles})


def record_for(family, rule):
    return TenantRecord(
        tenant_id="synthetic-advanced-arrays", display_name="Synthetic array metadata",
        mode="enforce", input_check_cross_field="flag",
        output_check_cross_field="block", **{family: (rule,)},
    )


def evaluate(record, sample, stage):
    req = CheckRequest(application_id=record.tenant_id, policy_version="tenant",
                       content=sample, content_format="json")
    return asyncio.run(Engine([record.to_policy()], default_checkers()).check(req, stage))


@pytest.mark.parametrize("family,kind", [
    ("advanced_arithmetic", "invoice_total"),
    ("advanced_arithmetic", "shipment_quantity_sum"),
    ("advanced_identity", "order_id_match"),
    ("advanced_identity", "serial_unique"),
    ("advanced_conversion_time", "unit_load_kg_cap"),
    ("advanced_conversion_time", "cold_chain_excursion"),
])
@pytest.mark.parametrize("stage,action", [(Stage.INPUT, "flag"), (Stage.OUTPUT, "block")])
def test_advanced_real_json_array_samples_are_selectable_and_evaluable(family, kind, stage, action):
    rule = example_rule(family, kind)
    record = record_for(family, rule)
    row = record_rules(record)["business." + rule.rule_id]
    assert row["content_format"] == "json" and row["control_id"] == "cross_field"
    assert row["example_unavailable"] is None and row["look_alike_unavailable"] is None
    for key in ("example", "look_alike"):
        payload = json.loads(row[key])
        assert isinstance(payload["batch"], list)
        assert len(payload["batch"]) == 1
        assert isinstance(payload["batch"][0], dict)
        assert set(payload["batch"][0]) == set(rule.fields)
    violating = evaluate(record, row["example"], stage)
    assert violating.evaluation_status == "complete"
    assert {finding.rule_id for finding in violating.findings} == {"business." + rule.rule_id}
    assert violating.effective_action == action
    clean = evaluate(record, row["look_alike"], stage)
    assert clean.evaluation_status == "complete"
    assert clean.findings == ()
    assert clean.effective_action == "allow"


def test_advanced_numeric_pointer_mixed_siblings_stay_object_keys_not_arrays():
    rule = AdvancedIdentityRule(
        rule_id="mixed_key", kind="order_id_match",
        fields={"order_id": "/batch/0/order_id",
                "shipment_order_id": "/batch/current/shipment_order_id"},
    )
    record = record_for("advanced_identity", rule)
    row = record_rules(record)["business.mixed_key"]
    for sample in (row["example"], row["look_alike"]):
        document = json.loads(sample)
        assert isinstance(document["batch"], dict)
        assert set(document["batch"]) == {"0", "current"}
        assert evaluate(record, sample, Stage.INPUT).evaluation_status == "complete"
    assert {f.rule_id for f in evaluate(record, row["example"], Stage.INPUT).findings} == {"business.mixed_key"}
    assert evaluate(record, row["look_alike"], Stage.INPUT).findings == ()


def test_advanced_large_numeric_pointer_is_bounded_object_key():
    rule = example_rule("advanced_identity", "order_id_match")
    rule = rule.model_copy(update={"fields": {
        "order_id": "/batch/1000000000/order_id",
        "shipment_order_id": "/batch/1000000000/shipment_order_id",
    }})
    record = record_for("advanced_identity", rule)
    row = record_rules(record)["business." + rule.rule_id]
    assert isinstance(json.loads(row["example"])["batch"], dict)
    assert evaluate(record, row["example"], Stage.INPUT).effective_action == "flag"


def test_authenticated_tenant_saves_all_advanced_families_with_real_array_examples_and_stage_actions():
    secret = "advanced-array-examples-test-secret-0123456789"
    tenant_id = "demo-advanced-array"
    token = demo_key(tenant_id, secret)
    rec = TenantRecord(tenant_id=tenant_id, display_name="Synthetic advanced array tenant",
                       demo=True, overage="block", monthly_allowance=25,
                       key_hashes=(key_hash(token),))
    store = MemoryTenantStore((rec,))
    client = TestClient(create_app(
        engine=demo_engine(), credentials={},
        tenants=TenantService(store, default_checkers(), secret),
    ))
    headers = {"Authorization": "Bearer " + token}
    definitions = (
        ("advanced_arithmetic", example_rule("advanced_arithmetic", "invoice_total", 0)),
        ("advanced_identity", example_rule("advanced_identity", "order_id_match", 1)),
        ("advanced_conversion_time", example_rule("advanced_conversion_time", "unit_load_kg_cap", 2)),
    )
    changes = {family: [rule.model_dump(mode="json")] for family, rule in definitions}
    changes.update(input_check_cross_field="flag", output_check_cross_field="block")
    initial_revision = store.get(tenant_id).revision
    response = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": initial_revision, "changes": changes,
    })
    assert response.status_code == 200, response.text[:300]
    listing = client.get("/v1/applications/current", headers=headers)
    assert listing.status_code == 200
    saved = listing.json()["tenant"]
    assert saved["revision"] == initial_revision + 1
    assert all(saved[family][0]["fields"] == rule.model_dump(mode="json")["fields"]
               for family, rule in definitions)
    metadata = {row["rule_id"]: row for row in saved["record_rules"]}
    benign = {"batch": []}
    for index, (_family, rule) in enumerate(definitions):
        row = metadata["business." + rule.rule_id]
        assert row["example_unavailable"] is None
        assert row["look_alike_unavailable"] is None
        clean = json.loads(row["look_alike"])["batch"]
        assert len(clean) == index + 1
        benign["batch"].append(clean[index])
    bad = deepcopy(benign)
    bad["batch"][2]["load"] = 1500
    for stage, action in (("input", "flag"), ("output", "block")):
        for payload, expected in ((benign, set()), (bad, {"business.array_unit_load_kg_cap"})):
            result = client.post("/v1/checks/" + stage, headers=headers, json={
                "application_id": tenant_id, "content_format": "json",
                "content": json.dumps(payload),
            })
            assert result.status_code == 200, result.text[:300]
            data = result.json()
            assert data["evaluation_status"] == "complete"
            assert {f["rule_id"] for f in data["findings"] if f["rule_id"].startswith("business.")} == expected
            assert data["effective_action"] == (action if expected else "allow")
    assert len(store.get(tenant_id).advanced_arithmetic) == 1
    assert len(store.get(tenant_id).advanced_identity) == 1
    assert len(store.get(tenant_id).advanced_conversion_time) == 1
