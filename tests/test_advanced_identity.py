"""FC-038/039/040/048: real identity and tenant-trusted reference checks."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.hosted import TenantService
from guardoath.models import Action, AdvancedIdentityRule, CheckRequest, Policy, Rule, Stage
from guardoath.record_rules import record_rules
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


CASES = [
    ("FC-038", "order_id_match", {"order_id": "/order_id", "shipment_order_id": "/shipment_order_id"},
     (), {"order_id": "ORD1", "shipment_order_id": "ORD2"},
     {"order_id": "ORD1", "shipment_order_id": "ORD1"}),
    ("FC-039", "quarantine_exclusion", {"requested_lot": "/lot"},
     ("QLOT8",), {"lot": "QLOT8"}, {"lot": "LOT9"}),
    ("FC-040", "serial_unique", {"serials": "/serials"},
     (), {"serials": ["SER1", "SER1"]}, {"serials": ["SER1", "SER2"]}),
    ("FC-048", "authorized_scope_subset", {"requested_scopes": "/scopes"},
     ("read:item", "write:item"), {"scopes": ["read:item", "admin:write"]},
     {"scopes": ["read:item"]}),
]


def rule(case):
    _name, kind, pointers, trusted, *_samples = case
    return AdvancedIdentityRule(rule_id=kind, kind=kind, fields=pointers,
                                trusted_values=trusted)


def evaluate(rules, values, stage=Stage.INPUT):
    policy = Policy(application_id="identity", version="v1", mode="enforce",
                    rules=(Rule(checker_id="cross_field", on_finding=Action.BLOCK),),
                    advanced_identity=tuple(rules))
    request = CheckRequest(application_id="identity", policy_version="v1",
                           content=json.dumps(values), content_format="json")
    return asyncio.run(Engine([policy], default_checkers()).check(request, stage))


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_four_identity_rules_both_stages_positive_and_benign(case):
    item = rule(case)
    for stage in (Stage.INPUT, Stage.OUTPUT):
        bad = evaluate((item,), case[4], stage)
        assert bad.evaluation_status == "complete"
        assert {f.rule_id for f in bad.findings} == {"business." + item.rule_id}
        assert bad.effective_action == Action.BLOCK
        good = evaluate((item,), case[5], stage)
        assert good.evaluation_status == "complete"
        assert good.findings == () and good.effective_action == Action.ALLOW


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_missing_and_wrong_type_produce_findings(case):
    item = rule(case)
    for malformed in ({}, {next(iter(case[2].values()))[1:]: True}):
        result = evaluate((item,), malformed)
        assert result.evaluation_status == "complete"
        assert {f.rule_id for f in result.findings} == {"business." + item.rule_id}


def test_arrays_and_trusted_sets_are_bounded_and_not_submitted_authority():
    serial = rule(CASES[2])
    scopes = rule(CASES[3])
    assert evaluate((serial,), {"serials": ["X"] * 129}).effective_action == Action.BLOCK
    assert evaluate((serial,), {"serials": ["SER1", "SER1"]}).effective_action == Action.BLOCK
    assert evaluate((scopes,), {"scopes": ["admin:write"], "trusted_values": ["admin:write"]}).effective_action == Action.BLOCK
    assert evaluate((scopes,), {"scopes": ["read:item"]}).findings == ()


def test_invalid_trusted_reference_configuration_rejected():
    with pytest.raises(ValidationError):
        AdvancedIdentityRule(rule_id="lot", kind="quarantine_exclusion",
                             fields={"requested_lot": "/lot"})
    with pytest.raises(ValidationError):
        AdvancedIdentityRule(rule_id="scopes", kind="authorized_scope_subset",
                             fields={"requested_scopes": "/scopes"},
                             trusted_values=("admin:write", "admin:write"))
    with pytest.raises(ValidationError):
        AdvancedIdentityRule(rule_id="serials", kind="serial_unique",
                             fields={"serials": "/serials"}, trusted_values=("FAKE",))


def test_reference_backed_dashboard_never_exposes_trusted_samples():
    from types import SimpleNamespace
    items = (rule(CASES[1]), rule(CASES[3]))
    listed = record_rules(SimpleNamespace(comparisons=(), advanced_arithmetic=(),
                                          advanced_identity=items, required_sections=()))
    assert set(listed) == {"business.quarantine_exclusion", "business.authorized_scope_subset"}
    for entry in listed.values():
        assert entry["record_rule"]
        assert entry["example"] is None and entry["look_alike"] is None
        assert entry["example_unavailable"]["code"] == "private_tenant_reference"
        assert "QLOT8" not in json.dumps(entry)
        assert "read:item" not in json.dumps(entry)


def test_all_four_identity_findings_collected_and_independent_stage_settings():
    items = []
    bad = {}
    for case in CASES:
        item = rule(case)
        item = item.model_copy(update={"fields": {k: "/" + item.kind + "/" + v.lstrip("/")
                                         for k, v in item.fields.items()}})
        items.append(item)
        bad[item.kind] = case[4]
    for stage in (Stage.INPUT, Stage.OUTPUT):
        result = evaluate(items, bad, stage)
        assert result.evaluation_status == "complete"
        assert {f.rule_id for f in result.findings} == {
            "business." + case[1] for case in CASES
        }


def test_authenticated_tenant_persists_trusted_sets_and_controls_both_stages():
    secret = "advanced-identity-e2e-secret-0123456789"
    tenant = "demo-b"
    token = demo_key(tenant, secret)
    original = TenantRecord(tenant_id=tenant, display_name="Synthetic identity", demo=True,
                            overage="block", monthly_allowance=25, key_hashes=(key_hash(token),),
                            input_check_text_security="flag", output_check_text_security="flag")
    store = MemoryTenantStore((original,))
    client = TestClient(create_app(engine=demo_engine(), credentials={},
                                   tenants=TenantService(store, default_checkers(), secret)))
    h = {"Authorization": "Bearer " + token}
    settings = client.post("/v1/tenants/current/checks", headers=h, json={
        "revision": store.get(tenant).revision,
        "changes": {"input_check_cross_field": "flag", "output_check_cross_field": "flag",
                    "advanced_identity": [rule(case).model_dump(mode="json") for case in CASES],
                    "input_rule_actions": {"business.quarantine_exclusion": "block"},
                    "output_rule_actions": {"business.quarantine_exclusion": "off",
                                            "business.authorized_scope_subset": "allow"}},
    })
    assert settings.status_code == 200, settings.text[:300]
    assert len(store.get(tenant).advanced_identity) == 4
    data = {"order_id": "ORD1", "shipment_order_id": "ORD2", "lot": "QLOT8",
            "serials": ["SER1", "SER1"], "scopes": ["admin:write"]}
    for stage, count, action in (("input", 4, "block"), ("output", 3, "flag")):
        response = client.post("/v1/checks/" + stage, headers=h, json={
            "application_id": tenant, "content_format": "json", "content": json.dumps(data)})
        assert response.status_code == 200, response.text[:300]
        body = response.json()
        assert body["evaluation_status"] == "complete"
        assert len([f for f in body["findings"] if f["rule_id"].startswith("business.")]) == count
        assert body["effective_action"] == action
        assert "QLOT8" not in response.text
    assert store.get(tenant).used(usage_month()) == 2
