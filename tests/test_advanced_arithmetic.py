"""FC Batch A: eight exact-arithmetic tenant rules through the same cross_field Engine."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.hosted import TenantService
from guardoath.models import Action, AdvancedArithmeticRule, CheckRequest, Policy, Rule, Stage
from guardoath.record_rules import ARITHMETIC_EXAMPLES, record_rules
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


KINDS = (
    ("FC-031", "invoice_line_subtotal"),
    ("FC-032", "invoice_total"),
    ("FC-033", "tax_tolerance"),
    ("FC-034", "discount_fraction_cap"),
    ("FC-036", "inventory_conservation"),
    ("FC-037", "shipment_quantity_sum"),
    ("FC-041", "manufacturing_tolerance"),
    ("FC-046", "carbon_intensity"),
)


def make_rule(kind, prefix=""):
    values = ARITHMETIC_EXAMPLES[kind][0]
    return AdvancedArithmeticRule(rule_id=(prefix + kind)[:80], kind=kind,
                                  fields={name: "/" + name for name in values})


def engine(*rules, action=Action.BLOCK):
    policy = Policy(application_id="arithmetic", version="v1", mode="enforce",
                    rules=(Rule(checker_id="cross_field", on_finding=action),),
                    advanced_arithmetic=tuple(rules))
    return Engine([policy], default_checkers())


def check(engine_instance, payload, stage=Stage.INPUT):
    request = CheckRequest(application_id="arithmetic", policy_version="v1",
                           content=payload, content_format="json")
    return asyncio.run(engine_instance.check(request, stage))


@pytest.mark.parametrize("case,kind", KINDS, ids=[name for name, _ in KINDS])
def test_eight_rules_each_have_real_positive_benign_both_stages(case, kind):
    rule = make_rule(kind)
    listed = record_rules(SimpleNamespace(comparisons=(), required_sections=(), advanced_arithmetic=(rule,)))
    row = listed["business." + rule.rule_id]
    assert row["control_id"] == "cross_field" and row["record_rule"] is True
    assert row["content_format"] == "json" and row["configuration"]
    policy_engine = engine(rule)
    for stage in (Stage.INPUT, Stage.OUTPUT):
        unsafe = check(policy_engine, row["example"], stage)
        assert unsafe.evaluation_status == "complete", case
        assert {f.rule_id for f in unsafe.findings} == {"business." + rule.rule_id}
        assert unsafe.effective_action == Action.BLOCK
        clean = check(policy_engine, row["look_alike"], stage)
        assert clean.evaluation_status == "complete", (case, clean.checker_results)
        assert clean.findings == () and clean.effective_action == Action.ALLOW


@pytest.mark.parametrize("case,kind", KINDS, ids=[name for name, _ in KINDS])
def test_eight_rules_missing_wrong_type_and_boolean_all_fail_closed(case, kind):
    rule = make_rule(kind)
    clean = dict(ARITHMETIC_EXAMPLES[kind][0])
    for replacement in ("missing", "wrong-type", "boolean"):
        payload = dict(clean)
        key = next(iter(payload))
        if replacement == "missing":
            payload.pop(key)
        elif replacement == "wrong-type":
            payload[key] = "not a number or list"
        else:
            payload[key] = True
        result = check(engine(rule), json.dumps(payload))
        assert result.evaluation_status == "complete", case
        assert {f.rule_id for f in result.findings} == {"business." + kind}
        assert result.effective_action == Action.BLOCK
        assert "not a number" not in str(result.findings)


@pytest.mark.parametrize("kind,field", [
    ("discount_fraction_cap", "eligible_subtotal"),
    ("carbon_intensity", "output"),
])
def test_zero_denominator_never_passes(kind, field):
    rule = make_rule(kind)
    payload = dict(ARITHMETIC_EXAMPLES[kind][0])
    payload[field] = 0
    result = check(engine(rule), json.dumps(payload))
    assert {f.rule_id for f in result.findings} == {"business." + kind}


def test_tax_tolerance_boundary_and_exact_decimal_math():
    rule = make_rule("tax_tolerance")
    base = {"tax": 0.30, "base": 3, "rate": 0.1, "tolerance": 0}
    assert check(engine(rule), json.dumps(base)).findings == ()
    assert check(engine(rule), json.dumps({**base, "tax": 0.31})).effective_action == Action.BLOCK


def test_overflow_and_large_arrays_refuse_without_leaking_or_running_unbounded_work():
    rule = make_rule("invoice_line_subtotal")
    for lines in ([{"quantity": 1, "unit_price": 1}] * 129,
                  [{"quantity": 10**70, "unit_price": 1}]):
        result = check(engine(rule), json.dumps({"lines": lines, "subtotal": 1}))
        assert result.effective_action == Action.BLOCK
        assert {f.rule_id for f in result.findings} == {"business.invoice_line_subtotal"}


def test_multiple_rules_preserve_all_findings_and_both_stage_actions():
    invoice = make_rule("invoice_total", "invoice_")
    carbon = make_rule("carbon_intensity", "carbon_")
    # Avoid overlapping pointers for a single tenant's independent business rules.
    invoice = invoice.model_copy(update={"fields": {name: "/invoice/" + name for name in invoice.fields}})
    carbon = carbon.model_copy(update={"fields": {name: "/carbon/" + name for name in carbon.fields}})
    data = {"invoice": {**ARITHMETIC_EXAMPLES["invoice_total"][0], "total": 110},
            "carbon": {**ARITHMETIC_EXAMPLES["carbon_intensity"][0], "emissions": 1000}}
    result = check(engine(invoice, carbon), json.dumps(data))
    assert {f.rule_id for f in result.findings} == {"business.invoice_invoice_total",
                                                   "business.carbon_carbon_intensity"}
    assert result.effective_action == Action.BLOCK


def test_duplicate_and_ambiguous_advanced_rule_ids_or_pointers_refused():
    item = make_rule("invoice_total")
    with pytest.raises(ValidationError):
        Policy(application_id="arithmetic", version="v1", rules=(),
               advanced_arithmetic=(item, item))
    with pytest.raises(ValidationError):
        AdvancedArithmeticRule(rule_id="bad", kind="invoice_total", fields={"subtotal": "/value"})
    with pytest.raises(ValidationError):
        AdvancedArithmeticRule(rule_id="bad", kind="tax_tolerance",
                               fields={"tax": "/v", "base": "/v", "rate": "/r", "tolerance": "/t"})


def test_authenticated_saved_tenant_arithmetic_two_stage_actions_and_one_meter():
    secret = "advanced-arithmetic-e2e-secret-0123456789"
    name = "demo-a"
    token = demo_key(name, secret)
    customer = TenantRecord(tenant_id=name, display_name="Synthetic arithmetic", demo=True,
                            overage="block", monthly_allowance=20, key_hashes=(key_hash(token),),
                            input_check_text_security=Action.FLAG, output_check_text_security=Action.FLAG)
    store = MemoryTenantStore((customer,))
    service = TenantService(store, default_checkers(), secret)
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=service))
    headers = {"Authorization": "Bearer " + token}
    invoice = make_rule("invoice_total")
    carbon = make_rule("carbon_intensity")
    invoice = invoice.model_copy(update={"fields": {name: "/invoice/" + name for name in invoice.fields}})
    carbon = carbon.model_copy(update={"fields": {name: "/carbon/" + name for name in carbon.fields}})
    config = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": store.get(name).revision,
        "changes": {"input_check_cross_field": "flag", "output_check_cross_field": "flag",
                    "advanced_arithmetic": [r.model_dump(mode="json") for r in (invoice, carbon)],
                    "input_rule_actions": {"business.invoice_total": "block", "business.carbon_intensity": "allow"},
                    "output_rule_actions": {"business.invoice_total": "off", "business.carbon_intensity": "flag"}},
    })
    assert config.status_code == 200, config.text[:300]
    rules = {r["rule_id"] for r in config.json()["record_rules"]}
    assert {"business.invoice_total", "business.carbon_intensity"} <= rules
    assert store.get(name).revision == 2
    data = {"invoice": {**ARITHMETIC_EXAMPLES["invoice_total"][0], "total": 110},
            "carbon": {**ARITHMETIC_EXAMPLES["carbon_intensity"][0], "emissions": 1000}}
    for stage, ids, action in (
        ("input", {"business.invoice_total", "business.carbon_intensity"}, "block"),
        ("output", {"business.carbon_intensity"}, "flag"),
    ):
        response = client.post("/v1/checks/" + stage, headers=headers, json={
            "application_id": name, "content_format": "json", "content": json.dumps(data)})
        assert response.status_code == 200, response.text[:300]
        body = response.json()
        assert body["evaluation_status"] == "complete"
        assert {f["rule_id"] for f in body["findings"] if f["rule_id"].startswith("business.")} == ids
        assert body["effective_action"] == action
        assert "1000" not in response.text
    assert store.get(name).used(usage_month()) == 2


def test_all_eight_advanced_rules_run_together_and_return_all_findings():
    rules = []
    payload = {}
    for _case, kind in KINDS:
        rule = make_rule(kind)
        rule = rule.model_copy(update={
            "fields": {name: "/" + kind + "/" + name for name in rule.fields}
        })
        clean, bad_role, bad_value = ARITHMETIC_EXAMPLES[kind]
        payload[kind] = {**clean, bad_role: bad_value}
        rules.append(rule)
    for stage in (Stage.INPUT, Stage.OUTPUT):
        result = check(engine(*rules), json.dumps(payload), stage)
        assert result.evaluation_status == "complete"
        assert {f.rule_id for f in result.findings} == {
            "business." + kind for _case, kind in KINDS
        }
        assert result.effective_action == Action.BLOCK
