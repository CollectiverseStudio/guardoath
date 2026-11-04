"""Per-rule settings on a tenant record (rule_actions): saved, refused and applied."""
import asyncio
import base64

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.dynamodb_store import from_item, to_item
from guardoath.engine import Engine
from guardoath.hosted import TenantService, rejection_message
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage
from guardoath.rule_catalog import RULES
from guardoath.tenant_record import EDITABLE_FIELDS, TENANT_POLICY_VERSION, TenantRecord, demo_defaults, demo_key, demo_records
from guardoath.tenant_store import MemoryTenantStore

CHECKERS = default_checkers(enable_pii=True)
SECRET = "rule-actions-test-demo-secret-0123456789"
FORCE = RULES["agent.git_force"].example
DROP = RULES["agent.sql_destructive"].example
IBAN = "Send the refund to IBAN DE89 3704 0044 0532 0130 00 please."
EMAIL_AND_IBAN = "Email jane@example.com about IBAN DE89 3704 0044 0532 0130 00."


def record(**fields):
    return TenantRecord(tenant_id="acme", display_name="Acme", **fields)


def run(rec, content, stage=Stage.INPUT):
    engine = Engine([rec.to_policy()], CHECKERS)
    request = CheckRequest(application_id=rec.tenant_id, policy_version=TENANT_POLICY_VERSION, content=content)
    return asyncio.run(engine.check(request, stage))


def ids(result):
    return {finding.rule_id for finding in result.findings}


# The record field


def test_rule_actions_is_an_editable_check_setting():
    assert "rule_actions" in EDITABLE_FIELDS
    assert record(check_lexical_guards="flag").rule_actions == {}
    assert demo_defaults()["rule_actions"] == {}


@pytest.mark.parametrize("rule_actions", [
    {"agent.not_a_rule": "off"}, {"pii.iban_code": "redact"}, {"agent.git_force": "on"},
    {"Agent.git_force": "off"}, {"agent": "off"}, {"agent.git_force": None},
])
def test_unknown_rules_and_actions_are_refused(rule_actions):
    with pytest.raises(ValidationError):
        record(check_lexical_guards="flag", rule_actions=rule_actions)


def test_an_unknown_rule_id_is_not_echoed_in_the_message():
    with pytest.raises(ValidationError) as error:
        record(check_lexical_guards="flag", rule_actions={"agent.secret_name_xyz": "off"})
    assert "secret_name_xyz" not in rejection_message(error.value)


def test_a_redacting_pii_check_allows_only_off_for_its_rules():
    record(check_pii="redact", rule_actions={"pii.iban_code": "off"})
    with pytest.raises(ValidationError, match="can only be turned off"):
        record(check_pii="redact", rule_actions={"pii.iban_code": "flag"})
    with pytest.raises(ValidationError, match="can only be turned off"):
        Policy(application_id="a", version="v", rules=(Rule(checker_id="pii", on_finding=Action.REDACT),),
               rule_actions={"pii.iban_code": "block"})


def test_rule_settings_go_to_the_engine_only_with_their_check():
    rec = record(check_secrets="flag", rule_actions={"agent.git_force": "block", "secrets.jwt": "off"})
    assert rec.to_policy().rule_actions == {"secrets.jwt": "off"}
    rec = record(check_lexical_guards="flag", check_secrets="flag", rule_actions={"agent.git_force": "block"})
    assert rec.to_policy().rule_actions == {"agent.git_force": "block"}
    # encoded_pii reuses the pii scan, so pii rule settings go with it too.
    rec = record(check_encoded_pii="flag", rule_actions={"pii.iban_code": "off"})
    assert rec.to_policy().rule_actions == {"pii.iban_code": "off"}


def test_rule_settings_survive_the_dynamodb_item():
    rec = record(check_lexical_guards="flag", rule_actions={"agent.git_force": "block", "pii.url": "off"})
    assert from_item(to_item(rec)).rule_actions == rec.rule_actions


# Applying them


def test_off_drops_the_rule_and_a_check_with_no_findings_passes():
    on = record(check_lexical_guards="block")
    assert ids(run(on, FORCE)) == {"agent.git_force"} and run(on, FORCE).recommended_action == "block"
    off = record(check_lexical_guards="block", rule_actions={"agent.git_force": "off"})
    result = run(off, FORCE)
    assert result.findings == () and result.recommended_action == "allow"
    assert result.checker_results[0].status == "passed" and result.evaluation_status == "complete"


def test_off_leaves_the_other_rules_of_the_check():
    rec = record(check_lexical_guards="flag", rule_actions={"agent.git_force": "off"})
    assert ids(run(rec, FORCE + " then " + DROP)) == {"agent.sql_destructive"}


def test_a_rule_action_replaces_the_check_action_for_that_rule_only():
    rec = record(check_lexical_guards="flag", rule_actions={"agent.git_force": "block"})
    assert run(rec, FORCE).recommended_action == "block"
    assert run(rec, DROP).recommended_action == "flag"
    rec = record(check_lexical_guards="block", rule_actions={"agent.git_force": "allow"})
    assert run(rec, FORCE).recommended_action == "allow" and ids(run(rec, FORCE)) == {"agent.git_force"}
    # The strongest action of all findings decides.
    assert run(rec, FORCE + " then " + DROP).recommended_action == "block"


def test_observe_mode_still_reports_rule_actions_without_enforcing():
    rec = record(mode="observe", check_lexical_guards="flag", rule_actions={"agent.git_force": "block"})
    result = run(rec, FORCE)
    assert result.recommended_action == "block" and result.effective_action == "allow"


def test_a_privacy_type_that_is_off_is_not_reported():
    assert ids(run(record(check_pii="flag"), EMAIL_AND_IBAN)) == {"pii.email_address", "pii.iban_code"}
    # The domain inside an email address is a URL too, so both are turned off to leave the address alone.
    rec = record(check_pii="flag", rule_actions={"pii.email_address": "off", "pii.url": "off"})
    assert ids(run(rec, EMAIL_AND_IBAN)) == {"pii.iban_code"}
    assert "pii.iban_code" not in ids(run(record(check_pii="flag", rule_actions={"pii.iban_code": "off"}), IBAN))


def test_a_privacy_type_that_is_off_is_not_redacted():
    rec = record(mode="enforce", check_pii="redact", rule_actions={"pii.email_address": "off", "pii.url": "off"})
    result = run(rec, EMAIL_AND_IBAN)
    assert result.redaction.status == "ready"
    assert result.redaction.content == "Email jane@example.com about IBAN [REDACTED_IBAN_CODE]."
    assert all(check.status == "passed" for check in result.redaction.validation_results)


def test_a_privacy_type_that_is_off_is_not_reported_inside_encoded_text():
    payload = "base64: " + base64.b64encode("IBAN DE89 3704 0044 0532 0130 00".encode()).decode()
    on = record(check_encoded_pii="flag")
    assert "pii.encoded_iban_code" in ids(run(on, payload))
    off = record(check_encoded_pii="flag", rule_actions={"pii.iban_code": "off"})
    assert "pii.encoded_iban_code" not in ids(run(off, payload))


def test_a_secret_rule_that_is_off_is_not_reported():
    jwt = RULES["secrets.jwt"].example
    assert "secrets.jwt" in ids(run(record(check_secrets="block"), jwt))
    result = run(record(check_secrets="block", rule_actions={"secrets.jwt": "off"}), jwt)
    assert "secrets.jwt" not in ids(result)


def test_rule_actions_change_nothing_for_policies_without_them():
    policy = Policy(application_id="a", version="v", rules=(Rule(checker_id="lexical_guards", on_finding=Action.BLOCK),))
    engine = Engine([policy], CHECKERS)
    result = asyncio.run(engine.check(CheckRequest(application_id="a", policy_version="v", content=FORCE), Stage.INPUT))
    assert ids(result) == {"agent.git_force"} and result.recommended_action == "block"


# The hosted API


def client_and_service():
    tenants = TenantService(MemoryTenantStore(demo_records(key_secret=SECRET)), CHECKERS, SECRET)
    return TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants)), tenants


def auth(token):
    return {"Authorization": "Bearer " + token}


def change(client, token, revision, changes):
    return client.post("/v1/tenants/current/checks", headers=auth(token), json={"revision": revision, "changes": changes})


def summary(client, token):
    return client.get("/v1/applications/current", headers=auth(token)).json()["tenant"]


def test_rule_actions_are_saved_shown_and_applied():
    client, _ = client_and_service()
    token = demo_key("demo1", SECRET)
    before = summary(client, token)
    assert before["rule_actions"] == {}
    email = "Please email jane@example.com about my order."
    assert client.post("/v1/checks/input", headers=auth(token), json={
        "application_id": "demo1", "content": email}).json()["effective_action"] == "block"
    # The domain inside the address is also a URL, so both rules are turned off.
    off = {"pii.email_address": "off", "pii.url": "off"}
    response = change(client, token, before["revision"], {"rule_actions": off})
    assert response.status_code == 200
    assert response.json()["rule_actions"] == off
    assert summary(client, token)["rule_actions"] == off
    result = client.post("/v1/checks/input", headers=auth(token), json={"application_id": "demo1", "content": email}).json()
    assert result["effective_action"] == "allow"
    assert "pii.email_address" not in {finding["rule_id"] for finding in result["findings"]}


def test_unknown_rules_are_refused_with_422_and_nothing_is_saved():
    client, tenants = client_and_service()
    token = demo_key("demo2", SECRET)
    before = summary(client, token)
    response = change(client, token, before["revision"], {"rule_actions": {"agent.secret_name_xyz": "off"}})
    assert response.status_code == 422 and "secret_name_xyz" not in response.text
    assert tenants.store.get("demo2").revision == before["revision"] and tenants.store.get("demo2").rule_actions == {}


def test_resetting_a_demo_user_clears_its_rule_actions():
    client, tenants = client_and_service()
    token = demo_key("demo3", SECRET)
    before = summary(client, token)
    assert change(client, token, before["revision"], {"rule_actions": {"agent.git_force": "block"}}).status_code == 200
    assert client.post("/v1/tenants/current/reset", headers=auth(token), json={}).status_code == 200
    assert tenants.store.get("demo3").rule_actions == {}


def test_an_encoded_privacy_rule_has_its_own_action_even_while_pii_redacts():
    # 2026-11-04: pii.encoded_* rules belong to encoded_pii, which never redacts, so they may have any action.
    payload = "base64: " + base64.b64encode("IBAN DE89 3704 0044 0532 0130 00".encode()).decode()
    rec = record(mode="enforce", check_pii="redact", check_encoded_pii="flag",
                 rule_actions={"pii.encoded_iban_code": "block"})
    result = run(rec, payload)
    assert "pii.encoded_iban_code" in ids(result) and result.recommended_action == "block"
    assert run(record(check_encoded_pii="flag"), payload).recommended_action == "flag"
    with pytest.raises(ValidationError, match="can only be turned off"):
        record(check_pii="redact", check_encoded_pii="flag", rule_actions={"pii.iban_code": "block"})


def test_document_check_rules_can_have_their_own_action():
    graph = RULES["structure.graph_cycle"]
    rec = record(check_graph="flag", graph={}, rule_actions={"structure.graph_cycle": "block"})
    request = CheckRequest(application_id="acme", policy_version=TENANT_POLICY_VERSION, content=graph.example,
                           content_format="json")
    result = asyncio.run(Engine([rec.to_policy()], CHECKERS).check(request, Stage.INPUT))
    assert ids(result) == {"structure.graph_cycle"} and result.recommended_action == "block"
