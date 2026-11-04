"""A tenant record's own rules: each field comparison (business.<rule_id>) and required section
(completeness.<rule_id>) is a rule of that record only (guardoath.record_rules, 2026-11-04).

Covers: every rule's generated example is reported and its look-alike is not, on each stage it runs on (all 30
templates in docs/field-comparisons-rule-inventory.json and the demo users' own comparisons and sections); each rule
can be off, allow, flag or block on its own; settings may name only the record's own rules, never another tenant's or a
removed one; rule ids cannot reach a static rule; tenants sharing one engine keep their own settings; the public
GET /v1/controls lists no tenant's rules, and a tenant sees only its own.
"""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.hosted import TenantService, rejection_message
from guardoath.models import Action, CheckRequest, FieldComparison, Policy, Rule, Stage
from guardoath.record_rules import record_rules
from guardoath.rule_catalog import RULES
from guardoath.tenant_record import TENANT_POLICY_VERSION, TenantRecord, demo_defaults, demo_key, demo_records
from guardoath.tenant_store import MemoryTenantStore

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = json.loads((ROOT / "docs" / "field-comparisons-rule-inventory.json").read_text(encoding="utf-8"))["rules"]
CHECKERS = default_checkers()
DEFAULTS = demo_defaults()
SECRET = "-".join(["record", "rules", "demo", "key", "material", "0123456789abcdef"])


def record(tenant_id="acme", **fields):
    return TenantRecord(tenant_id=tenant_id, display_name=tenant_id.title(), mode="enforce", **fields)


def run(rec, content, content_format, stage):
    request = CheckRequest(application_id=rec.tenant_id, policy_version=TENANT_POLICY_VERSION, content=content,
                           content_format=content_format)
    return asyncio.run(Engine([rec.to_policy()], CHECKERS).check(request, stage))


def ids(result):
    return {finding.rule_id for finding in result.findings}


def stages(rule):
    return [Stage(stage) for stage in rule["stages"]]


@pytest.mark.parametrize("case", INVENTORY, ids=lambda case: case["id"])
def test_each_template_as_a_records_only_comparison_reports_its_example_and_not_its_look_alike(case):
    rec = record(check_cross_field="block", comparisons=[case["comparison"]])
    rule = record_rules(rec)["business." + case["rule_id"]]
    assert rule["control_id"] == "cross_field" and rule["content_format"] == "json" and rule["record_rule"] is True
    for stage in stages(rule):
        reported = run(rec, rule["example"], "json", stage)
        assert reported.evaluation_status == "complete" and ids(reported) == {"business." + case["rule_id"]}
        assert reported.recommended_action == Action.BLOCK
        clean = run(rec, rule["look_alike"], "json", stage)
        assert clean.evaluation_status == "complete" and clean.findings == ()
        assert clean.recommended_action == Action.ALLOW


def test_all_30_templates_on_one_record_each_example_reports_its_own_rule():
    rec = record(check_cross_field="flag", comparisons=[case["comparison"] for case in INVENTORY])
    rules = record_rules(rec)
    assert len(rules) == 30
    for case in INVENTORY:
        rule = rules["business." + case["rule_id"]]
        assert "business." + case["rule_id"] in ids(run(rec, rule["example"], "json", Stage.INPUT))
        assert "business." + case["rule_id"] not in ids(run(rec, rule["look_alike"], "json", Stage.OUTPUT))


def test_the_demo_users_own_comparisons_and_sections_are_rules_with_working_examples():
    rec = record(check_cross_field="flag", check_completeness="flag", comparisons=DEFAULTS["comparisons"],
                 required_sections=DEFAULTS["required_sections"])
    rules = record_rules(rec)
    assert sorted(rules) == ["business.date_order", "business.refund_limit", "completeness.next_steps",
                             "completeness.request_summary", "completeness.response_summary"]
    for rule_id, rule in rules.items():
        check = rule["control_id"]
        for stage in stages(rule):
            assert rule_id in ids(run(rec, rule["example"], rule["content_format"], stage)), (rule_id, stage)
            clean = run(rec, rule["look_alike"], rule["content_format"], stage)
            assert not {f.rule_id for f in clean.findings if f.rule_id.startswith(rule_id.split(".")[0] + ".")}, rule_id
        assert rule["title"] in rule["use_case"] and rule["use_case"].startswith("Turn this on")
        assert rule["configuration"]
    assert rules["business.refund_limit"]["title"] == "Comparison refund_limit: /refund_amount \u2264 /purchase_amount (number)"


@pytest.mark.parametrize("action", ["off", "allow", "flag", "block"])
def test_each_comparison_can_have_its_own_action(action):
    rec = record(check_cross_field="flag", comparisons=DEFAULTS["comparisons"],
                 rule_actions={"business.refund_limit": action})
    rules = record_rules(rec)
    refund = run(rec, rules["business.refund_limit"]["example"], "json", Stage.INPUT)
    if action == "off":
        assert refund.findings == () and refund.recommended_action == Action.ALLOW
    else:
        assert ids(refund) == {"business.refund_limit"} and refund.recommended_action == Action(action)
    # The other comparison keeps the check's action.
    order = run(rec, rules["business.date_order"]["example"], "json", Stage.INPUT)
    assert ids(order) == {"business.date_order"} and order.recommended_action == Action.FLAG


def test_settings_may_name_only_this_records_own_rules():
    record(check_cross_field="flag", comparisons=DEFAULTS["comparisons"], rule_actions={"business.date_order": "block"})
    record(check_completeness="flag", required_sections=DEFAULTS["required_sections"],
           rule_actions={"completeness.next_steps": "off"})
    for actions in ({"business.not_on_this_record": "block"}, {"completeness.date_order": "off"}):
        with pytest.raises(ValidationError) as error:
            record(check_cross_field="flag", comparisons=DEFAULTS["comparisons"], rule_actions=actions)
        assert "not_on_this_record" not in rejection_message(error.value)
    # Removing a comparison while keeping its setting is refused, so a setting never outlives its rule.
    with pytest.raises(ValidationError, match="Unknown rule id"):
        record(check_secrets="flag", rule_actions={"business.date_order": "block"})


def test_record_rule_ids_are_lowercase_and_cannot_name_a_static_rule():
    for rule_id in ("Refund-Limit", "refund.limit", "9refund"):
        with pytest.raises(ValidationError, match="lowercase"):
            record(check_cross_field="flag", comparisons=[{**DEFAULTS["comparisons"][1], "rule_id": rule_id}])
        # Engine policies (not tenant records) keep the wider pattern.
        FieldComparison(**{**DEFAULTS["comparisons"][1], "rule_id": rule_id})
    for rule_id in ("ledger_currency", "ledger_unbalanced", "invalid_json"):
        assert "business." + rule_id in RULES
        with pytest.raises(ValidationError, match="another check"):
            record(check_cross_field="flag", comparisons=[{**DEFAULTS["comparisons"][1], "rule_id": rule_id}])


def test_tenants_sharing_an_engine_keep_their_own_comparisons_and_settings():
    comparison = DEFAULTS["comparisons"][1]
    blocking = record("acme", check_cross_field="flag", comparisons=[comparison], rule_actions={"business.refund_limit": "block"})
    allowing = record("beta", check_cross_field="flag", comparisons=[comparison], rule_actions={"business.refund_limit": "allow"})
    example = record_rules(blocking)["business.refund_limit"]["example"]
    engine = Engine([blocking.to_policy(), allowing.to_policy()], CHECKERS)

    async def both():
        return await asyncio.gather(*(engine.check(CheckRequest(application_id=owner, policy_version=TENANT_POLICY_VERSION,
                                                                content=example, content_format="json"), Stage.INPUT)
                                      for owner in ("acme", "beta") for _ in range(5)))

    results = asyncio.run(both())
    assert [r.recommended_action for r in results] == [Action.BLOCK] * 5 + [Action.ALLOW] * 5
    assert all(ids(r) == {"business.refund_limit"} for r in results)
    # A tenant cannot name another tenant's comparison.
    only_acme = record("acme", check_cross_field="flag", comparisons=[{**comparison, "rule_id": "acme_only"}])
    assert "business.acme_only" in record_rules(only_acme)
    with pytest.raises(ValidationError):
        record("beta", check_cross_field="flag", comparisons=[comparison], rule_actions={"business.acme_only": "block"})


def client_and_service():
    tenants = TenantService(MemoryTenantStore(demo_records(key_secret=SECRET)), CHECKERS, SECRET)
    return TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants)), tenants


def auth(token):
    return {"Authorization": "Bearer " + token}


def summary(client, token):
    return client.get("/v1/applications/current", headers=auth(token)).json()["tenant"]


def test_the_api_shows_each_tenant_only_its_own_rules_and_never_publicly():
    client, _ = client_and_service()
    one, two = demo_key("demo1", SECRET), demo_key("demo2", SECRET)
    before = summary(client, one)
    assert sorted(r["rule_id"] for r in before["record_rules"]) == ["business.date_order", "business.refund_limit",
        "completeness.next_steps", "completeness.request_summary", "completeness.response_summary"]
    added = [*DEFAULTS["comparisons"], {"rule_id": "demo1_only", "left": "/a", "right": "/b", "operator": "le"}]
    response = client.post("/v1/tenants/current/checks", headers=auth(one), json={
        "revision": before["revision"], "changes": {"comparisons": added, "input_rule_actions": {"business.demo1_only": "block"}, "output_rule_actions": {"business.demo1_only": "block"}}})
    assert response.status_code == 200
    assert "business.demo1_only" in {r["rule_id"] for r in response.json()["record_rules"]}
    assert "business.demo1_only" not in {r["rule_id"] for r in summary(client, two)["record_rules"]}
    # demo2 cannot set demo1's comparison; the message does not repeat it.
    refused = client.post("/v1/tenants/current/checks", headers=auth(two), json={
        "revision": summary(client, two)["revision"], "changes": {"input_rule_actions": {"business.demo1_only": "block"}, "output_rule_actions": {"business.demo1_only": "block"}}})
    assert refused.status_code == 422 and "demo1_only" not in refused.text
    # The public rule list never includes a tenant's own rules.
    public = client.get("/v1/controls").text
    assert "demo1_only" not in public and "business.date_order" not in public and "business.invalid_json" in public


def test_the_dashboard_lists_a_tenants_own_rules_with_their_configuration():
    script = (ROOT / "guardoath" / "static" / "dashboard.js").read_text(encoding="utf-8")
    for text in ["application.tenant.record_rules", "record_rules || []", '["Configuration: ", rule.configuration]',
                 "Needs these checks on too: "]:
        assert text in script, text
