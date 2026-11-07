"""Tenant-owned PSI batch comparison: real Engine, policy, safe responses and API."""
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
from guardoath.models import Action, AdvancedStatisticalDriftRule, CheckRequest, Policy, Rule, Stage
from guardoath.record_rules import record_rules
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


def rule():
    return AdvancedStatisticalDriftRule(
        rule_id="income_drift", observations_pointer="/income_samples",
        reference_id="synthetic-reference", reference_version="v1",
        bin_edges=("0", "10", "20", "30"), counts=(10, 10, 10),
        psi_threshold="0.15")


GOOD = [5] * 10 + [15] * 10 + [25] * 10
SHIFT = [5] + [15] + [25] * 28


def run(payload, *, stage=Stage.INPUT, action=Action.BLOCK, rules=None):
    policy = Policy(application_id="owner-a", version="v1", mode="enforce",
                    rules=(Rule(checker_id="cross_field", on_finding=action),),
                    advanced_statistical_drift=tuple(rules or (rule(),)))
    request = CheckRequest(application_id="owner-a", policy_version="v1",
                           content=json.dumps(payload), content_format="json")
    return asyncio.run(Engine([policy], default_checkers()).check(request, stage))


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_baseline_and_shift_both_stages_no_silent_clipping(stage):
    initial = list(GOOD)
    clean = run({"income_samples": initial}, stage=stage)
    assert clean.evaluation_status == "complete" and clean.findings == ()
    assert clean.effective_action == Action.ALLOW and initial == GOOD
    shifted = run({"income_samples": SHIFT}, stage=stage)
    assert shifted.evaluation_status == "complete"
    assert shifted.effective_action == Action.BLOCK
    assert {f.rule_id for f in shifted.findings} == {"business.income_drift"}
    assert "synthetic-reference" not in shifted.model_dump_json()


@pytest.mark.parametrize("payload", [
    {}, {"income_samples": "not-array"}, {"income_samples": [True] * 30},
    {"income_samples": [500] * 30}, {"income_samples": [5] * 29},
    {"income_samples": None},
])
def test_missing_invalid_outside_and_insufficient_report_distinct_findings(payload):
    result = run(payload)
    assert {f.rule_id for f in result.findings} == {"business.income_drift"}
    assert result.effective_action == Action.BLOCK
    if payload.get("income_samples") == [5] * 29:
        assert "Insufficient" in result.findings[0].explanation


def test_two_rules_return_all_findings_without_spilling_reference_counts():
    first = rule()
    second = first.model_copy(update={"rule_id": "another_batch", "observations_pointer": "/other"})
    result = run({"income_samples": SHIFT, "other": [5] * 29}, rules=(first, second))
    assert {f.rule_id for f in result.findings} == {
        "business.income_drift", "business.another_batch"}
    assert "synthetic-reference" not in result.model_dump_json()


@pytest.mark.parametrize("field,change", [
    ("psi_threshold", "-0.1"), ("psi_threshold", "nan"),
    ("bin_edges", ("0", "20", "10", "30")),
    ("counts", (True, 10, 19)),
    ("observations_pointer", "income_samples"),
    ("reference_id", ""),
    ("stages", (Stage.INPUT, Stage.INPUT)),
])
def test_tenant_settings_reject_invalid_baseline_and_threshold(field, change):
    raw = rule().model_dump(mode="json")
    raw[field] = change
    with pytest.raises(ValidationError):
        AdvancedStatisticalDriftRule.model_validate(raw)


def test_rule_is_private_and_dynamically_listed():
    stored = SimpleNamespace(comparisons=(), advanced_arithmetic=(),
                             advanced_identity=(), advanced_conversion_time=(),
                             advanced_trusted_reference=(),
                             advanced_statistical_drift=(rule(),), required_sections=())
    listed = record_rules(stored)
    entry = listed["business.income_drift"]
    assert entry["record_rule"] and entry["example"] is None
    assert "synthetic-reference" not in json.dumps(entry)
    assert "10" not in entry["configuration"]


def test_real_authenticated_tenant_api_save_stage_actions_off_and_isolation():
    secret = "statistical-drift-demo-private-secret"
    tenant, other = "drift-a", "drift-b"
    token, other_token = demo_key(tenant, secret), demo_key(other, secret)
    records = (
        TenantRecord(tenant_id=tenant, display_name="Drift", demo=True,
                     monthly_allowance=12, overage="block", key_hashes=(key_hash(token),),
                     input_check_text_security="flag", output_check_text_security="flag"),
        TenantRecord(tenant_id=other, display_name="Other", demo=True,
                     monthly_allowance=12, overage="block", key_hashes=(key_hash(other_token),),
                     input_check_text_security="flag", output_check_text_security="flag"),
    )
    store = MemoryTenantStore(records)
    client = TestClient(create_app(engine=demo_engine(), credentials={},
                                   tenants=TenantService(store, default_checkers(), secret)))
    headers = {"Authorization": "Bearer " + token}
    saved = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": store.get(tenant).revision,
        "changes": {
            "advanced_statistical_drift": [rule().model_dump(mode="json")],
            "input_check_cross_field": "block",
            "output_check_cross_field": "flag",
            "output_rule_actions": {"business.income_drift": "off"},
        }})
    assert saved.status_code == 200, saved.text[:250]
    assert len(store.get(tenant).advanced_statistical_drift) == 1
    assert "business.income_drift" in {v["rule_id"] for v in saved.json()["record_rules"]}
    for stage, expected in (("input", "block"), ("output", "allow")):
        response = client.post("/v1/checks/" + stage, headers=headers, json={
            "application_id": tenant, "content_format": "json",
            "content": json.dumps({"income_samples": SHIFT}),
        })
        assert response.status_code == 200, response.text[:250]
        body = response.json()
        assert body["effective_action"] == expected
        if stage == "input":
            assert "business.income_drift" in {f["rule_id"] for f in body["findings"]}
        else:
            assert not any(f["rule_id"] == "business.income_drift" for f in body["findings"])
        assert "synthetic-reference" not in response.text
    unauthorized = client.post("/v1/checks/input",
        headers={"Authorization": "Bearer " + other_token},
        json={"application_id": tenant, "content_format": "json",
              "content": json.dumps({"income_samples": SHIFT})})
    assert unauthorized.status_code == 403
    assert store.get(tenant).used(usage_month()) == 2
    assert store.get(other).used(usage_month()) == 0
