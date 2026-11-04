"""Input and output settings on one tenant record (2026-11-04).

Each check has two fields, input_check_<id> and output_check_<id> (input-only checks have no output field), and the
rule settings are two maps, input_rule_actions and output_rule_actions. Records stored before then hold one
check_<id> field and one rule_actions map for both stages; they are converted when read, so nothing changes until a
stage is set differently. Only Block stops a message or answer (Greg, 2026-11-04): see the dashboard and guide tests.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.dynamodb_store import from_item, to_item
from guardoath.engine import Engine
from guardoath.hosted import TenantService
from guardoath.models import Action, CheckRequest, Stage
from guardoath.tenant_record import (CHECK_FIELDS, CHECK_IDS, EDITABLE_FIELDS, INPUT_CHECK_FIELDS, INPUT_ONLY_CHECK_IDS,
                                     LEGACY_FIELDS, OUTPUT_CHECK_FIELDS, RULE_ACTION_FIELDS, TENANT_POLICY_VERSION,
                                     TenantRecord, demo_key, demo_records)
from guardoath.tenant_store import MemoryTenantStore

CHECKERS = default_checkers(enable_pii=True)
SECRET = "stage-settings-test-demo-secret-0123456789"
EMAIL = "Please email jane@example.com about my order."


def record(**fields):
    return TenantRecord(tenant_id="acme", display_name="Acme", **fields)


def run(rec, content, stage):
    engine = Engine([rec.to_policy()], CHECKERS)
    request = CheckRequest(application_id=rec.tenant_id, policy_version=TENANT_POLICY_VERSION, content=content)
    return asyncio.run(engine.check(request, stage))


def ids(result):
    return {finding.rule_id for finding in result.findings}


# The record fields


def test_every_check_has_an_input_field_and_every_two_stage_check_an_output_field():
    assert INPUT_CHECK_FIELDS == tuple("input_check_" + c for c in CHECK_IDS)
    assert OUTPUT_CHECK_FIELDS == tuple("output_check_" + c for c in CHECK_IDS if c not in INPUT_ONLY_CHECK_IDS)
    assert len(INPUT_CHECK_FIELDS) == 17 and len(OUTPUT_CHECK_FIELDS) == 16
    assert CHECK_FIELDS == INPUT_CHECK_FIELDS + OUTPUT_CHECK_FIELDS
    assert RULE_ACTION_FIELDS == ("input_rule_actions", "output_rule_actions")
    for name in CHECK_FIELDS:
        assert TenantRecord.model_fields[name].default is None
    assert "output_check_azure_prompt_shields" not in TenantRecord.model_fields


def test_the_old_field_names_are_not_record_fields_and_cannot_be_changed():
    assert LEGACY_FIELDS == {"check_" + c for c in CHECK_IDS} | {"rule_actions"}
    assert not LEGACY_FIELDS & set(TenantRecord.model_fields)
    assert not LEGACY_FIELDS & EDITABLE_FIELDS
    assert set(CHECK_FIELDS) | set(RULE_ACTION_FIELDS) <= EDITABLE_FIELDS


def test_a_check_can_run_on_one_stage_only():
    rec = record(input_check_secrets="flag", output_check_pii="block")
    assert rec.checks() == {"pii": (None, Action.BLOCK), "secrets": (Action.FLAG, None)}
    rules = {rule.checker_id: rule for rule in rec.to_policy().rules}
    assert rules["pii"].stages == (Stage.OUTPUT,) and rules["secrets"].stages == (Stage.INPUT,)
    assert ids(run(rec, EMAIL, Stage.INPUT)) == set()
    assert "pii.email_address" in ids(run(rec, EMAIL, Stage.OUTPUT))


def test_input_allow_and_output_block_on_the_same_check():
    rec = record(input_check_pii="allow", output_check_pii="block")
    incoming, outgoing = run(rec, EMAIL, Stage.INPUT), run(rec, EMAIL, Stage.OUTPUT)
    assert "pii.email_address" in ids(incoming) and incoming.effective_action == Action.ALLOW
    assert "pii.email_address" in ids(outgoing) and outgoing.effective_action == Action.BLOCK


def test_input_flag_and_output_block_report_the_same_finding_with_different_decisions():
    rec = record(input_check_pii="flag", output_check_pii="block")
    assert run(rec, EMAIL, Stage.INPUT).effective_action == Action.FLAG
    assert run(rec, EMAIL, Stage.OUTPUT).effective_action == Action.BLOCK


def test_an_input_only_check_has_no_output_action():
    rec = record(input_check_secrets="flag")
    assert rec.action("azure_prompt_shields", Stage.OUTPUT) is None
    with pytest.raises(ValidationError):
        record(input_check_secrets="flag", output_check_azure_prompt_shields="flag")


def test_redact_is_refused_for_other_checks_on_either_stage():
    for field in ("input_check_secrets", "output_check_secrets"):
        with pytest.raises(ValidationError, match="Only the pii check can redact"):
            record(**{field: "redact"})


# Per-stage rule settings


def test_a_rule_can_be_off_on_input_and_still_block_on_output():
    rec = record(input_check_pii="block", output_check_pii="block",
                 input_rule_actions={"pii.email_address": "off", "pii.url": "off"})
    assert run(rec, EMAIL, Stage.INPUT).effective_action == Action.ALLOW
    outgoing = run(rec, EMAIL, Stage.OUTPUT)
    assert "pii.email_address" in ids(outgoing) and outgoing.effective_action == Action.BLOCK
    policy = rec.to_policy()
    assert policy.input_rule_actions == {"pii.email_address": "off", "pii.url": "off"} and policy.output_rule_actions == {}


def test_a_rule_can_have_its_own_action_on_one_stage():
    rec = record(input_check_pii="block", output_check_pii="block", output_rule_actions={"pii.email_address": "allow",
                                                                                         "pii.url": "allow"})
    assert run(rec, EMAIL, Stage.INPUT).effective_action == Action.BLOCK
    assert run(rec, EMAIL, Stage.OUTPUT).effective_action == Action.ALLOW


def test_a_redacting_stage_allows_only_off_for_its_pii_rules_and_the_other_stage_is_free():
    with pytest.raises(ValidationError, match="redacts on input"):
        record(input_check_pii="redact", output_check_pii="block", input_rule_actions={"pii.email_address": "flag"})
    rec = record(input_check_pii="redact", output_check_pii="block", input_rule_actions={"pii.email_address": "off"},
                 output_rule_actions={"pii.email_address": "flag"})
    assert rec.to_policy().output_rule_actions == {"pii.email_address": "flag"}


def test_rule_settings_on_a_stage_where_the_check_is_off_are_kept_but_unused():
    rec = record(input_check_secrets="flag", output_rule_actions={"pii.email_address": "off"})
    assert rec.output_rule_actions == {"pii.email_address": "off"} and rec.to_policy().output_rule_actions == {}


def test_unknown_rule_ids_are_refused_on_either_stage_without_echo():
    for field in ("input_rule_actions", "output_rule_actions"):
        with pytest.raises(ValidationError) as error:
            record(input_check_secrets="flag", **{field: {"pii.secret_name_xyz": "off"}})
        assert "secret_name_xyz" not in str(error.value)


# Records stored before 2026-11-04


LEGACY = {"tenant_id": "acme", "display_name": "Acme", "revision": 4, "check_pii": "block", "check_secrets": "flag",
          "check_azure_prompt_shields": None, "rule_actions": {"pii.url": "off"}}


def test_a_stored_record_with_the_old_names_runs_exactly_as_before_on_both_stages():
    rec = TenantRecord.model_validate(LEGACY)
    assert rec.input_check_pii == rec.output_check_pii == Action.BLOCK
    assert rec.input_check_secrets == rec.output_check_secrets == Action.FLAG
    assert rec.input_rule_actions == rec.output_rule_actions == {"pii.url": "off"}
    assert rec.revision == 4
    dumped = rec.model_dump(mode="json")
    assert not LEGACY_FIELDS & set(dumped)


def test_an_old_input_only_check_converts_to_input_only():
    rec = TenantRecord.model_validate({**LEGACY, "check_azure_prompt_shields": "flag"})
    assert rec.input_check_azure_prompt_shields == Action.FLAG and rec.action("azure_prompt_shields", Stage.OUTPUT) is None


def test_a_dynamodb_item_holding_the_old_names_is_read_and_saved_with_the_new_ones():
    new = TenantRecord.model_validate(LEGACY)
    item = to_item(new)
    stored = json.loads(item["record"]["S"])
    for name in LEGACY_FIELDS:
        stored.pop(name, None)
    for name in [*CHECK_FIELDS, *RULE_ACTION_FIELDS]:
        stored.pop(name, None)
    stored.update({key: value for key, value in LEGACY.items() if key in LEGACY_FIELDS})
    old_item = {**item, "record": {"S": json.dumps(stored)}}
    read = from_item(old_item)
    assert read.input_check_pii == read.output_check_pii == Action.BLOCK
    assert read.input_rule_actions == read.output_rule_actions == {"pii.url": "off"}
    assert not LEGACY_FIELDS & set(json.loads(to_item(read)["record"]["S"]))


# The hosted API


def client_and_service():
    tenants = TenantService(MemoryTenantStore(demo_records(key_secret=SECRET)), CHECKERS, SECRET)
    return TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants)), tenants


def auth(token):
    return {"Authorization": "Bearer " + token}


def test_the_summary_shows_each_stage_separately():
    client, _ = client_and_service()
    tenant = client.get("/v1/applications/current", headers=auth(demo_key("demo1", SECRET))).json()["tenant"]
    assert len(tenant["input_checks"]) == 17 and len(tenant["output_checks"]) == 16
    assert "azure_prompt_shields" not in tenant["output_checks"]
    assert tenant["input_checks"]["pii"] == tenant["output_checks"]["pii"] == "block"
    assert tenant["input_rule_actions"] == tenant["output_rule_actions"] == {}
    assert "checks" not in tenant and "rule_actions" not in tenant


def test_a_demo_user_sets_input_and_output_separately_and_each_stage_runs_its_own():
    client, tenants = client_and_service()
    token = demo_key("demo1", SECRET)
    revision = tenants.store.get("demo1").revision
    response = client.post("/v1/tenants/current/checks", headers=auth(token), json={"revision": revision, "changes": {
        "input_check_pii": "flag", "output_rule_actions": {"pii.email_address": "allow", "pii.url": "allow"}}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["input_checks"]["pii"] == "flag" and body["output_checks"]["pii"] == "block"
    assert body["output_rule_actions"] == {"pii.email_address": "allow", "pii.url": "allow"} and body["input_rule_actions"] == {}
    incoming = client.post("/v1/checks/input", headers=auth(token), json={"application_id": "demo1", "content": EMAIL}).json()
    outgoing = client.post("/v1/checks/output", headers=auth(token), json={"application_id": "demo1", "content": EMAIL}).json()
    assert incoming["effective_action"] == "flag" and outgoing["effective_action"] == "allow"
    assert tenants.store.get("demo2").input_check_pii == Action.BLOCK


@pytest.mark.parametrize("changes", [{"check_pii": "flag"}, {"rule_actions": {"pii.url": "off"}},
                                     {"check_pii": "flag", "input_check_secrets": "flag"}])
def test_the_old_names_are_refused_with_the_new_names_in_the_message(changes):
    client, tenants = client_and_service()
    before = tenants.store.get("demo1")
    response = client.post("/v1/tenants/current/checks", headers=auth(demo_key("demo1", SECRET)),
                           json={"revision": before.revision, "changes": changes})
    assert response.status_code == 422
    assert "input_check_<id>" in response.json()["detail"] and "output_rule_actions" in response.json()["detail"]
    assert tenants.store.get("demo1") == before


def test_reset_puts_both_stages_back():
    client, tenants = client_and_service()
    token = demo_key("demo1", SECRET)
    revision = tenants.store.get("demo1").revision
    assert client.post("/v1/tenants/current/checks", headers=auth(token), json={"revision": revision, "changes": {
        "output_check_pii": None, "input_rule_actions": {"pii.url": "off"}}}).status_code == 200
    assert client.post("/v1/tenants/current/reset", headers=auth(token), json={}).status_code == 200
    rec = tenants.store.get("demo1")
    assert rec.output_check_pii == Action.BLOCK and rec.input_rule_actions == {}
