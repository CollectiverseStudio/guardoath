"""FC-035/042/043/050: real Engine, saved tenant/API, bounded false-positive controls."""
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
from guardoath.models import (Action, AdvancedConversionTimeRule, CheckRequest,
                             Policy, Rule, Stage)
from guardoath.record_rules import record_rules
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


CASES = (
    ("FC-035", "currency_conversion_cap",
     {"payment_amount": "/payment_amount", "payment_currency": "/payment_currency",
      "invoice_amount": "/invoice_amount", "invoice_currency": "/invoice_currency"},
     {"payment_amount": 110, "payment_currency": "EUR", "invoice_amount": 100,
      "invoice_currency": "USD"},
     {"payment_amount": 80, "payment_currency": "EUR", "invoice_amount": 100,
      "invoice_currency": "USD"}),
    ("FC-042", "unit_load_kg_cap",
     {"load": "/load", "load_unit": "/load_unit", "max_kg": "/max_kg"},
     {"load": 1500, "load_unit": "lb", "max_kg": 600},
     {"load": 1000, "load_unit": "lb", "max_kg": 600}),
    ("FC-043", "cold_chain_excursion",
     {"excursion_start": "/excursion_start", "excursion_end": "/excursion_end",
      "allowed_minutes": "/allowed_minutes"},
     {"excursion_start": "2026-11-06T10:00:00Z",
      "excursion_end": "2026-11-06T10:45:00Z", "allowed_minutes": 30},
     {"excursion_start": "2026-11-06T10:00:00Z",
      "excursion_end": "2026-11-06T10:15:00Z", "allowed_minutes": 30}),
    ("FC-050", "lifecycle_chronology",
     {"order_at": "/order_at", "shipment_at": "/shipment_at",
      "delivery_at": "/delivery_at", "return_at": "/return_at"},
     {"order_at": "2026-11-06T09:00:00Z", "shipment_at": "2026-11-06T12:00:00Z",
      "delivery_at": "2026-11-06T11:00:00Z"},
     {"order_at": "2026-11-06T09:00:00Z", "shipment_at": "2026-11-06T10:00:00Z",
      "delivery_at": "2026-11-06T11:00:00Z"}),
)
FX = dict(source_currency="EUR", destination_currency="USD",
          trusted_rate_numerator=6, trusted_rate_denominator=5,
          rate_reference="internal-fx-v2026-11-06",
          rate_asof="2026-11-06T00:00:00Z")


def rule(case):
    return AdvancedConversionTimeRule(
        rule_id=case[1], kind=case[1], fields=case[2],
        **(FX if case[1] == "currency_conversion_cap" else {}))


def evaluate(rules, payload, stage=Stage.INPUT):
    policy = Policy(application_id="fc-c", version="v1", mode="enforce",
                    rules=(Rule(checker_id="cross_field", on_finding=Action.BLOCK),),
                    advanced_conversion_time=tuple(rules))
    request = CheckRequest(application_id="fc-c", policy_version="v1",
                           content=json.dumps(payload), content_format="json")
    return asyncio.run(Engine([policy], default_checkers()).check(request, stage))


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_four_conversion_time_rules_both_stages_positive_and_benign(case):
    item = rule(case)
    for stage in (Stage.INPUT, Stage.OUTPUT):
        bad = evaluate((item,), case[3], stage)
        assert bad.evaluation_status == "complete"
        assert {f.rule_id for f in bad.findings} == {"business." + item.rule_id}
        assert bad.effective_action == Action.BLOCK
        assert str(case[3]) not in str(bad.findings)
        good = evaluate((item,), case[4], stage)
        assert good.evaluation_status == "complete"
        assert good.findings == () and good.effective_action == Action.ALLOW


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_all_missing_or_wrong_type_fields_fail_closed(case):
    item = rule(case)
    for name, pointer in case[2].items():
        # FC-050 return_at is optional before a return actually happens.
        if name == "return_at":
            invalid = dict(case[4], return_at=True)
        else:
            invalid = dict(case[4])
            invalid.pop(pointer[1:])
        response = evaluate((item,), invalid)
        assert {f.rule_id for f in response.findings} == {"business." + item.rule_id}
    wrong = {key: True for key in case[4]}
    assert {f.rule_id for f in evaluate((item,), wrong).findings} == {"business." + item.rule_id}


def test_fx_requires_versioned_tenant_reference_and_refuses_submitted_rates():
    currency = rule(CASES[0])
    assert {f.rule_id for f in evaluate((currency,), {
        **CASES[0][3], "trusted_rate_numerator": 1, "trusted_rate_denominator": 10,
    }).findings} == {"business.currency_conversion_cap"}
    assert evaluate((currency,), {
        **CASES[0][4], "trusted_rate_numerator": 100000000,
    }).findings == ()
    for bad_metadata in (
        {"rate_asof": "2026-11-06"},
        {"trusted_rate_numerator": 0},
        {"trusted_rate_denominator": 0},
        {"rate_reference": ""},
        {"source_currency": "USD"},
    ):
        with pytest.raises(ValidationError):
            AdvancedConversionTimeRule(rule_id="fx", kind=currency.kind, fields=currency.fields,
                                       **{**FX, **bad_metadata})
    with pytest.raises(ValidationError):
        AdvancedConversionTimeRule(rule_id="wrong", kind="unit_load_kg_cap",
                                   fields=CASES[1][2], **FX)


def test_exact_unit_conversion_and_unsupported_units():
    item = rule(CASES[1])
    clean = {"load": 1000, "load_unit": "lb", "max_kg": 453.59237}
    assert evaluate((item,), clean).findings == ()
    assert {f.rule_id for f in evaluate((item,), {
        **clean, "max_kg": 453.59236,
    }).findings} == {"business.unit_load_kg_cap"}
    for unit in ("lbs", "grams", "LB", None):
        assert evaluate((item,), {**clean, "load_unit": unit}).effective_action == Action.BLOCK
    assert evaluate((item,), {"load": 500, "load_unit": "kg", "max_kg": 500}).findings == ()


def test_time_offsets_exact_boundary_and_chronology_return_optional():
    excursion = rule(CASES[2])
    assert evaluate((excursion,), {
        "excursion_start": "2026-11-06T10:00:00Z",
        "excursion_end": "2026-11-06T10:30:00+00:00",
        "allowed_minutes": 30,
    }).findings == ()
    assert evaluate((excursion,), {
        "excursion_start": "2026-11-06T10:00:00Z",
        "excursion_end": "2026-11-06T11:00:00+01:00",
        "allowed_minutes": 0,
    }).findings == ()
    assert evaluate((excursion,), {
        "excursion_start": "2026-11-06T10:00:00Z",
        "excursion_end": "2026-11-06T09:59:59Z",
        "allowed_minutes": 60,
    }).effective_action == Action.BLOCK
    timeline = rule(CASES[3])
    assert evaluate((timeline,), CASES[3][4]).findings == ()
    assert evaluate((timeline,), {**CASES[3][4],
                                   "return_at": "2026-11-06T11:30:00Z"}).findings == ()
    assert evaluate((timeline,), {**CASES[3][4],
                                   "return_at": "2026-11-06T09:30:00Z"}).effective_action == Action.BLOCK
    assert evaluate((timeline,), {**CASES[3][4], "return_at": None}).effective_action == Action.BLOCK


def test_model_rejects_pointer_overlap_stage_duplicates_and_ambiguous_ids():
    case = CASES[2]
    with pytest.raises(ValidationError):
        AdvancedConversionTimeRule(rule_id="bad", kind=case[1],
            fields={"excursion_start": "/same", "excursion_end": "/same",
                    "allowed_minutes": "/allowed_minutes"})
    with pytest.raises(ValidationError):
        AdvancedConversionTimeRule(rule_id="bad", kind=case[1],
            fields=case[2], stages=(Stage.INPUT, Stage.INPUT))
    with pytest.raises(ValidationError):
        Policy(application_id="fc-c", version="v1", rules=(),
               advanced_arithmetic=(), advanced_conversion_time=(rule(case), rule(case)))


def test_four_rules_all_findings_shared_engine_and_private_dashboard_metadata():
    items = []
    violations = {}
    for case in CASES:
        item = rule(case)
        item = item.model_copy(update={
            "fields": {name: "/" + item.kind + pointer for name, pointer in item.fields.items()},
        })
        items.append(item)
        violations[item.kind] = case[3]
    for stage in (Stage.INPUT, Stage.OUTPUT):
        result = evaluate(items, violations, stage)
        assert result.evaluation_status == "complete"
        assert {f.rule_id for f in result.findings} == {
            "business." + case[1] for case in CASES
        }
    listed = record_rules(SimpleNamespace(comparisons=(), required_sections=(),
        advanced_arithmetic=(), advanced_identity=(), advanced_conversion_time=tuple(items)))
    assert len(listed) == 4
    assert listed["business.currency_conversion_cap"]["example"] is None
    assert listed["business.currency_conversion_cap"]["look_alike"] is None
    assert listed["business.currency_conversion_cap"]["example_unavailable"]["code"] == "private_tenant_reference"
    assert "internal-fx-v2026-11-06" not in json.dumps(listed)
    assert "trusted_rate_numerator" not in json.dumps(listed)


def test_authenticated_tenant_api_saves_all_four_and_enforces_stage_actions():
    secret = "advanced-conversion-e2e-secret-0123456789"
    tenant, other = "demo-b", "demo-c"
    token, other_token = demo_key(tenant, secret), demo_key(other, secret)
    records = (
        TenantRecord(tenant_id=tenant, display_name="FC C synthetic", demo=True,
                     overage="block", monthly_allowance=25, key_hashes=(key_hash(token),),
                     input_check_text_security="flag", output_check_text_security="flag"),
        TenantRecord(tenant_id=other, display_name="Unrelated tenant", demo=True,
                     overage="block", monthly_allowance=25, key_hashes=(key_hash(other_token),),
                     input_check_text_security="flag", output_check_text_security="flag"),
    )
    store = MemoryTenantStore(records)
    client = TestClient(create_app(engine=demo_engine(), credentials={},
                                   tenants=TenantService(store, default_checkers(), secret)))
    h = {"Authorization": "Bearer " + token}
    response = client.post("/v1/tenants/current/references", headers=h, json={
        "revision": store.get(tenant).revision,
        "changes": {
            "input_check_cross_field": "flag", "output_check_cross_field": "flag",
            "advanced_conversion_time": [rule(case).model_dump(mode="json") for case in CASES],
            "input_rule_actions": {"business.currency_conversion_cap": "block"},
            "output_rule_actions": {"business.currency_conversion_cap": "off",
                                    "business.unit_load_kg_cap": "allow"},
        },
    })
    assert response.status_code == 200, response.text[:300]
    assert len(store.get(tenant).advanced_conversion_time) == 4
    violations = {}
    for case in CASES:
        violations.update(case[3])
    for stage, count, action in (("input", 4, "block"), ("output", 3, "flag")):
        result = client.post("/v1/checks/" + stage, headers=h, json={
            "application_id": tenant, "content_format": "json",
            "content": json.dumps(violations),
        })
        assert result.status_code == 200, result.text[:300]
        body = result.json()
        assert body["evaluation_status"] == "complete"
        assert len([f for f in body["findings"] if f["rule_id"].startswith("business.")]) == count
        assert body["effective_action"] == action
        assert "internal-fx-v2026-11-06" not in result.text
    intruder = client.post("/v1/checks/input", headers={"Authorization": "Bearer " + other_token},
                           json={"application_id": tenant, "content_format": "json",
                                 "content": json.dumps(violations)})
    assert intruder.status_code == 403
    assert store.get(tenant).used(usage_month()) == 2
    assert store.get(other).used(usage_month()) == 0


@pytest.mark.parametrize("explicit_event_root", [None, "/returns/0"], ids=["inferred", "configured"])
def test_fc050_distinct_root_empty_return_array_is_optional_but_recorded_item_is_not(explicit_event_root):
    """An empty return collection is benign; an existing incomplete element is not."""
    timestamp_fields = {
        "order_at": "/order/at",
        "shipment_at": "/shipment/at",
        "delivery_at": "/delivery/at",
        "return_at": "/returns/0/at",
    }
    item = AdvancedConversionTimeRule(
        rule_id="array_return", kind="lifecycle_chronology", fields=timestamp_fields,
        return_event_pointer=explicit_event_root)
    required = {
        "order": {"at": "2026-11-06T09:00:00Z"},
        "shipment": {"at": "2026-11-06T10:00:00Z"},
        "delivery": {"at": "2026-11-06T11:00:00Z"},
    }
    for stage in (Stage.INPUT, Stage.OUTPUT):
        for absent in ({**required}, {**required, "returns": []}):
            clean = evaluate((item,), absent, stage)
            assert clean.evaluation_status == "complete"
            assert clean.findings == () and clean.effective_action == Action.ALLOW
        for malformed in (
            {**required, "returns": {}},
            {**required, "returns": {"unexpected": 1}},
            {**required, "returns": "not_a_collection"},
            {**required, "returns": [{}]},
            {**required, "returns": [{"at": None}]},
            {**required, "returns": [{"at": "2026-11-06T08:00:00Z"}]},
        ):
            blocked = evaluate((item,), malformed, stage)
            assert blocked.evaluation_status == "complete"
            assert {finding.rule_id for finding in blocked.findings} == {
                "business.array_return"
            }
            assert blocked.effective_action == Action.BLOCK
            assert "2026-11-06" not in str(blocked.findings)
        valid_return = {**required, "returns": [{"at": "2026-11-06T12:00:00Z"}]}
        assert evaluate((item,), valid_return, stage).findings == ()
