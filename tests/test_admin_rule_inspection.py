"""Owner rule inspection binds UI changes to authoritative stage settings, not an assumed default."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guardoath.admin_audit import AdminSensitiveReadAuditor
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.admin_rule_inspection import (
    RuleInspectionUnavailable, inspect_tenant_rule_action)
from guardoath.tenant_record import TenantRecord
from guardoath.tenant_store import MemoryTenantStore

OWNER = "12345678-1234-1234-1234-123456789012"


class Audit:
    def __init__(self):
        self.events = []
        self.fail = False

    def put_item(self, **kwargs):
        if self.fail:
            raise RuntimeError("Audit table unavailable")
        self.events.append(kwargs)


def tenant(name, **kwargs):
    return TenantRecord.model_validate({
        "tenant_id": name, "display_name": "Synthetic admin rule test",
        "subscription_status": "inactive", **kwargs
    })


def setup():
    db = Audit()
    store = MemoryTenantStore((tenant("usr-one", input_check_secrets="block",
                                     input_rule_actions={"secrets.credential_assignment": "flag"}),
                               tenant("usr-two", input_check_secrets=None,
                                      input_rule_actions={"secrets.credential_assignment": "block"})))
    def verify(token):
        if token == "owner":
            return VerifiedAdmin(OWNER, frozenset({"owner"}), True)
        if token == "customer":
            return VerifiedAdmin(OWNER, frozenset({"customer"}), True)
        if token == "no-mfa":
            return VerifiedAdmin(OWNER, frozenset({"owner"}), False)
        return None
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=verify, tenant_store=store,
        read_auditor=AdminSensitiveReadAuditor(db, "admin-audit", signing_key=b"x"*32),
        require_persistent_read_audit=True))
    return TestClient(app), db, store


def target(tenant_id="usr-one", stage="input", rule_id="secrets.credential_assignment"):
    return "/v1/admin/tenants/" + tenant_id + "/rule-action?stage=" + stage + "&rule_id=" + rule_id


def test_read_inspection_is_revision_bound_redacted_and_audited():
    client, db, _ = setup()
    response = client.get(target(), headers={"X-GuardOath-Admin-Session": "owner"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["tenant_id"] == "usr-one" and result["revision"] == 1
    assert result["check_id"] == "secrets" and result["check_action"] == "block"
    assert result["rule_action"] == "flag" and result["eligibility"] == "configured_not_execution_evidence"
    assert result["tenant_policies_changed"] is False
    assert len(result["read_audit_event_id"]) == 64
    assert len(db.events) == 1
    assert db.events[0]["Item"]["read_resource"]["S"] == "tenant.detail"
    for secret in ("key_hashes", "payer_id", "stripe_subscription_id", "privacy_patterns", "confidential_terms"):
        assert secret not in response.text


def test_stage_off_wins_even_when_rule_override_is_block():
    client, db, _ = setup()
    headers = {"X-GuardOath-Admin-Session": "owner"}
    disabled = client.get(target("usr-two"), headers=headers).json()
    assert disabled["check_action"] is None
    assert disabled["rule_action"] == "block"
    assert disabled["eligibility"] == "check_off"
    other_stage = client.get(target("usr-one", "output"), headers=headers).json()
    assert other_stage["check_action"] is None
    assert other_stage["rule_action"] == "same_as_check"
    assert other_stage["eligibility"] == "check_off"
    assert len(db.events) == 2


@pytest.mark.parametrize("credential,expected", [
    (None,401), ("demo",401), ("customer",403), ("no-mfa",403)])
def test_no_anonymous_customer_demo_or_non_mfa_rule_inspection(credential,expected):
    client, db, _ = setup()
    headers = {} if credential is None else {"X-GuardOath-Admin-Session": credential}
    assert client.get(target(),headers=headers).status_code == expected
    assert not db.events


def test_invalid_selection_and_missing_tenant_never_expose_other_account():
    client, db, _ = setup()
    headers = {"X-GuardOath-Admin-Session": "owner"}
    assert client.get(target("usr-missing"), headers=headers).status_code == 404
    assert client.get(target(rule_id="secrets.unknown"), headers=headers).status_code == 404
    assert client.get(target(stage="other"),headers=headers).status_code == 422
    assert client.get(target(rule_id="weird..id"),headers=headers).status_code == 422
    assert len(db.events) == 2  # Missing tenant and unknown published rule audited


def test_bad_tenant_record_and_audit_outage_fail_closed_without_leaking():
    client, db, store = setup()
    headers = {"X-GuardOath-Admin-Session": "owner"}
    old_get = store.get
    store.get = lambda key: old_get("usr-two")
    assert client.get(target("usr-one"), headers=headers).status_code == 503
    store.get = old_get
    db.fail = True
    assert client.get(target(), headers=headers).status_code == 503
    assert len(db.events) == 1
    invalid = tenant("usr-three",input_check_secrets="block").model_copy(update={
        "input_rule_actions": {"secrets.credential_assignment": "invalid"}
    })
    with pytest.raises(RuleInspectionUnavailable):
        inspect_tenant_rule_action(invalid, tenant_id="usr-three", stage="input",rule_id="secrets.credential_assignment")
