"""One revisioned tenant/Engine across ordinary and three advanced FC families at scale.

Uses genuine API authentication and 500-rule requests, not mock checkers or
an artificial comparison count ceiling. Synthetic inputs only.
"""
import json

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore

TENANT = "mixed-scale-fc"
SECRET = "mixed-field-comparison-scale-test-secret-0123456789"


def _family(i):
    return ("comparisons", "advanced_arithmetic",
            "advanced_identity", "advanced_conversion_time")[i % 4]


def _portfolio(size):
    definitions = {family: [] for family in (
        "comparisons", "advanced_arithmetic", "advanced_identity", "advanced_conversion_time")}
    for i in range(size):
        group = f"r{i:03d}"
        rule_id = f"mixed_{i:03d}"
        family = _family(i)
        if family == "comparisons":
            rule = {"rule_id": rule_id, "left": f"/{group}/a", "right": f"/{group}/b",
                    "operator": "le", "value_type": "number", "stages": ["input", "output"]}
        elif family == "advanced_arithmetic":
            rule = {"rule_id": rule_id, "kind": "discount_fraction_cap",
                    "fields": {name: f"/{group}/{name}" for name in
                               ("discount", "eligible_subtotal", "max_fraction")},
                    "stages": ["input", "output"]}
        elif family == "advanced_identity":
            rule = {"rule_id": rule_id, "kind": "order_id_match",
                    "fields": {name: f"/{group}/{name}" for name in
                               ("order_id", "shipment_order_id")},
                    "stages": ["input", "output"]}
        else:
            rule = {"rule_id": rule_id, "kind": "unit_load_kg_cap",
                    "fields": {name: f"/{group}/{name}" for name in
                               ("load", "load_unit", "max_kg")},
                    "stages": ["input", "output"]}
        definitions[family].append(rule)
    return definitions


def _payload(size, violating=False):
    document = {}
    for i in range(size):
        family = _family(i)
        if family == "comparisons":
            values = {"a": 1, "b": 2}
            if violating and i in (0, size - 1):
                values["a"] = 9
        elif family == "advanced_arithmetic":
            values = {"discount": 10, "eligible_subtotal": 100, "max_fraction": 0.2}
            if violating and i == size - 1:
                values["discount"] = 30
        elif family == "advanced_identity":
            values = {"order_id": "ORDER-1", "shipment_order_id": "ORDER-1"}
            if violating and i == size - 1:
                values["shipment_order_id"] = "ORDER-2"
        else:
            values = {"load": 10, "load_unit": "kg", "max_kg": 20}
            if violating and i == size - 1:
                values["load"] = 50
        document[f"r{i:03d}"] = values
    return json.dumps(document, separators=(",", ":"))


def _client():
    token = demo_key(TENANT, SECRET)
    record = TenantRecord(tenant_id=TENANT, display_name="Synthetic mixed FC scale",
                          mode="enforce", demo=True, overage="block", monthly_allowance=30,
                          key_hashes=(key_hash(token),),
                          input_check_text_security="flag",
                          output_check_text_security="flag")
    store = MemoryTenantStore((record,))
    service = TenantService(store, default_checkers(), SECRET)
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=service))
    return client, store, {"Authorization": "Bearer " + token}


@pytest.mark.parametrize("size", [30, 51, 100, 250, 500])
def test_mixed_four_family_scale_is_one_revisioned_tenant_and_engine(size):
    client, store, headers = _client()
    old = store.get(TENANT)
    definitions = _portfolio(size)
    first, last = "business.mixed_000", f"business.mixed_{size - 1:03d}"
    changes = {**definitions, "input_check_cross_field": "flag",
               "output_check_cross_field": "block",
               "input_rule_actions": {first: "allow"},
               "output_rule_actions": {last: "flag"}}
    saved = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": old.revision, "changes": changes,
    })
    assert saved.status_code == 200, (size, saved.text[:200])
    after = store.get(TENANT)
    assert after.revision == old.revision + 1
    for family, rules in definitions.items():
        assert len(getattr(after, family)) == len(rules)
        assert [x.rule_id for x in getattr(after, family)] == [x["rule_id"] for x in rules]
    listed = saved.json()["record_rules"]
    portfolio_ids = {f"business.mixed_{i:03d}" for i in range(size)}
    assert portfolio_ids <= {r["rule_id"] for r in listed}
    assert after.input_rule_actions[first] == "allow"
    assert after.output_rule_actions[last] == "flag"

    stale = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": old.revision, "changes": {"input_check_cross_field": "block"},
    })
    assert stale.status_code == 409
    assert store.get(TENANT) == after

    for stage, expected_action in (("input", "flag"), ("output", "block")):
        bad = client.post(f"/v1/checks/{stage}", headers=headers, json={
            "application_id": TENANT, "content_format": "json",
            "content": _payload(size, violating=True),
        })
        assert bad.status_code == 200, (size, stage, bad.text[:200])
        result = bad.json()
        found = {f["rule_id"] for f in result["findings"] if f["rule_id"] in portfolio_ids}
        assert found == {first, last}, (size, stage, found)
        assert result["evaluation_status"] == "complete", (size, stage, result.get("checker_results"))
        assert result["recommended_action"] == expected_action
        assert result["effective_action"] == expected_action
        assert '"discount":30' not in bad.text

        good = client.post(f"/v1/checks/{stage}", headers=headers, json={
            "application_id": TENANT, "content_format": "json",
            "content": _payload(size),
        })
        assert good.status_code == 200
        clean = good.json()
        assert clean["evaluation_status"] == "complete"
        assert not ({f["rule_id"] for f in clean["findings"]} & portfolio_ids)
        assert clean["effective_action"] == "allow"
