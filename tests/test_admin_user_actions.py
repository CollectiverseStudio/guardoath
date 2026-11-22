"""Cognito owner account access writes: audit, provider authority, replay and UI integration."""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor
from guardoath.admin_audit_integrity import verify_audit
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.admin_users import CognitoUserDirectory


OWNER = "12345678-1234-1234-1234-123456789012"
TARGET = "87654321-4321-4321-4321-210987654321"
EMAIL = "person@example.com"
POOL = "us-east-1_TestPool"
KEY = b"s" * 32


class DDB:
    def __init__(self):
        self.rows = {}
        self.fail_writes = False
        self.fail_outcome = False

    def put_item(self, **kw):
        if self.fail_writes or (self.fail_outcome and
                               kw["Item"]["operation"]["S"].endswith(".outcome")):
            raise RuntimeError("DynamoDB audit failed")
        ident = kw["Item"]["event_id"]["S"]
        if ident in self.rows:
            class Conflict(Exception):
                response = {"Error": {"Code": "ConditionalCheckFailedException"}}
            raise Conflict()
        assert kw["ConditionExpression"] == "attribute_not_exists(event_id)"
        self.rows[ident] = kw["Item"]

    def get_item(self, **kw):
        item = self.rows.get(kw["Key"]["event_id"]["S"])
        return {"Item": item} if item is not None else {}


class Cognito:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.target_sub = TARGET
        self.groups = []
        self.calls = []
        self.fail_after = False
        self.ambiguous = False

    def _entry(self):
        return {"Username": "opaque-target-id", "Enabled": self.enabled,
                "UserStatus": "CONFIRMED",
                "Attributes": [{"Name": "email", "Value": EMAIL},
                               {"Name": "sub", "Value": self.target_sub}]}

    def list_users(self, **kw):
        self.calls.append("list")
        assert kw == {"UserPoolId": POOL, "Filter": 'email = "' + EMAIL + '"', "Limit": 2}
        return {"Users": [self._entry()] * (2 if self.ambiguous else 1)}

    def admin_get_user(self, **kw):
        self.calls.append("get")
        assert kw == {"UserPoolId": POOL, "Username": "opaque-target-id"}
        if self.fail_after and any(x in self.calls for x in ("disable", "enable")):
            raise RuntimeError("Cognito verification unavailable")
        entry = self._entry()
        return {"Username": entry["Username"], "Enabled": entry["Enabled"],
                "UserStatus": entry["UserStatus"],
                "UserAttributes": entry["Attributes"]}

    def admin_list_groups_for_user(self, **kw):
        self.calls.append("groups")
        assert kw["Limit"] == 60 and kw["UserPoolId"] == POOL
        return {"Groups": [{"GroupName": name} for name in self.groups]}

    def admin_disable_user(self, **kw):
        self.calls.append("disable")
        self.enabled = False

    def admin_enable_user(self, **kw):
        self.calls.append("enable")
        self.enabled = True


def make(db=None, provider=None):
    db, provider = db or DDB(), provider or Cognito()
    directory = CognitoUserDirectory(provider, POOL, KEY, clock=lambda: 100)
    audit = AdminSensitiveReadAuditor(db, "audit_table", signing_key=KEY,
                                      clock=lambda: datetime(2026, 11, 20, tzinfo=timezone.utc))
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(OWNER, frozenset({"owner"}), True)
                                    if token == "owner" else None,
        admin_user_directory=directory, read_auditor=audit,
        audit_reader=AdminAuditReader(db, "audit_table", verify_key=KEY)))
    return TestClient(app), db, provider


def body(action="disable"):
    return {"email": EMAIL, "action": action, "expected_enabled": action == "disable",
            "confirmation": action.upper() + " " + EMAIL,
            "reason": "Owner requested account access control",
            "idempotency_key": "accountaction00000001"}


HEADERS = {"X-GuardOath-Admin-Session": "owner", "Origin": "https://guardoath.com"}
URL = "/v1/admin/users/status"


def test_disable_has_signed_durable_intent_before_provider_and_independent_verified_outcome():
    client, db, provider = make()
    result = client.post(URL, json=body(), headers=HEADERS)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["status"] == "verified" and data["enabled"] is False
    assert provider.enabled is False and provider.calls == ["list", "get", "groups", "disable", "get"]
    intent = db.rows[data["intent_event_id"]]
    outcome = db.rows[data["audit_event_id"]]
    assert intent["operation"]["S"] == "cognito.user.status.intent"
    assert outcome["related_event_id"]["S"] == data["intent_event_id"]
    assert outcome["operation"]["S"] == "cognito.user.status.outcome"
    assert verify_audit(intent, KEY) and verify_audit(outcome, KEY)
    assert EMAIL not in str(db.rows) and "opaque-target-id" not in str(db.rows)
    fetched = client.get("/v1/admin/audit/" + data["audit_event_id"], headers=HEADERS)
    assert fetched.status_code == 200, fetched.text
    receipt = fetched.json()["audit"]
    assert receipt["integrity_status"] == "signed_verified"
    assert receipt["change"]["field"] == "cognito_enabled"
    assert receipt["change"]["before"] is True and receipt["change"]["after"] is False
    assert receipt["change"]["related_event_id"] == data["intent_event_id"]
    assert receipt["change"]["target_fingerprint"] == data["target_fingerprint"]


def test_enable_is_real_cognito_operation_but_never_grants_entitlement():
    client, db, provider = make(provider=Cognito(enabled=False))
    result = client.post(URL, json=body("enable"), headers=HEADERS)
    assert result.status_code == 200 and result.json()["enabled"] is True
    assert "enable" in provider.calls and provider.enabled is True
    assert "subscription" not in str(db.rows) and "tenant" not in result.json()



class EmailDriftCognito(Cognito):
    def __init__(self, *, phase, duplicate=False):
        super().__init__()
        self.phase, self.duplicate = phase, duplicate

    def admin_get_user(self, **kw):
        result = super().admin_get_user(**kw)
        written = "disable" in self.calls or "enable" in self.calls
        if self.phase == "before" or (self.phase == "after" and written):
            attributes = result["UserAttributes"]
            if self.duplicate:
                attributes.append({"Name": "email", "Value": "alternate@example.com"})
            else:
                for attribute in attributes:
                    if attribute["Name"] == "email":
                        attribute["Value"] = "alternate@example.com"
        return result


@pytest.mark.parametrize("duplicate", [False, True])
def test_preflight_rejects_changed_or_ambiguous_confirmed_email_without_cognito_write(duplicate):
    client, db, provider = make(provider=EmailDriftCognito(phase="before", duplicate=duplicate))
    result = client.post(URL, json=body(), headers=HEADERS)
    assert result.status_code == 503, result.text
    assert provider.calls == ["list", "get"]
    assert provider.enabled is True
    assert not any(row["operation"]["S"].endswith(".intent") for row in db.rows.values())


def test_postwrite_email_change_returns_indeterminate_signed_intent_not_false_success():
    client, db, provider = make(provider=EmailDriftCognito(phase="after"))
    result = client.post(URL, json=body(), headers=HEADERS)
    assert result.status_code == 503, result.text
    assert provider.enabled is False
    assert provider.calls == ["list", "get", "groups", "disable", "get"]
    assert sum(row["operation"]["S"].endswith(".intent") for row in db.rows.values()) == 1
    assert not any(row["operation"]["S"].endswith(".outcome") for row in db.rows.values())
    assert client.post(URL, json=body(), headers=HEADERS).status_code == 409
    assert provider.calls.count("disable") == 1


def test_case_insensitive_same_confirmed_email_is_not_false_drift():
    class CaseOnly(Cognito):
        def admin_get_user(self, **kw):
            result = super().admin_get_user(**kw)
            for attribute in result["UserAttributes"]:
                if attribute["Name"] == "email":
                    attribute["Value"] = EMAIL.upper()
            return result

    client, db, provider = make(provider=CaseOnly())
    result = client.post(URL, json=body(), headers=HEADERS)
    assert result.status_code == 200, result.text
    assert provider.enabled is False
    assert sum(row["operation"]["S"].endswith(".outcome") for row in db.rows.values()) == 1


@pytest.mark.parametrize("change", [
    {"confirmation": "DISABLE someone@example.com"},
    {"expected_enabled": False}, {"reason": "short"},
    {"idempotency_key": "bad"}, {"email": 'victim"@example.com'},
    {"action": "not-an-action"},
])
def test_invalid_or_unconfirmed_input_cannot_mutate(change):
    client, db, provider = make()
    post = {**body(), **change}
    assert client.post(URL, json=post, headers=HEADERS).status_code == 422
    assert provider.calls == []
    assert not any(e["operation"]["S"].endswith(".intent") for e in db.rows.values())


def test_rejects_self_and_any_owner_group_or_ambiguous_directory():
    for scenario in ("self", "owner", "ambiguous"):
        provider = Cognito()
        if scenario == "self": provider.target_sub = OWNER
        if scenario == "owner": provider.groups = ["GuardOathOwners"]
        if scenario == "ambiguous": provider.ambiguous = True
        client, db, provider = make(provider=provider)
        assert client.post(URL, json=body(), headers=HEADERS).status_code == 409
        assert "disable" not in provider.calls and "enable" not in provider.calls
        assert not any(r["operation"]["S"].endswith(".intent") for r in db.rows.values())


@pytest.mark.parametrize("provider_groups", [
    None,
    {"Groups": "not-a-list"},
    {"Groups": [{"GroupName": "TenantUsers"}] * 61},
    {"Groups": [{"GroupName": "TenantUsers"}, {"GroupName": "TenantUsers"}]},
    {"Groups": [{"GroupName": ""}]},
    {"Groups": [{"GroupName": "x" * 129}]},
    {"Groups": [{"GroupName": 7}]},
    {"Groups": [{"GroupName": "TenantUsers"}], "NextToken": ""},
    {"Groups": [{"GroupName": "TenantUsers"}], "NextToken": "another-page"},
])
def test_incomplete_or_ambiguous_cognito_membership_fails_closed_before_audited_intent(provider_groups):
    class InvalidMembership(Cognito):
        def admin_list_groups_for_user(self, **kw):
            super().admin_list_groups_for_user(**kw)
            return provider_groups

    client, db, provider = make(provider=InvalidMembership())
    result = client.post(URL, json=body(), headers=HEADERS)
    assert result.status_code == 503, result.text
    assert provider.calls == ["list", "get", "groups"]
    assert provider.enabled is True
    assert not any(row["operation"]["S"].endswith(".intent") for row in db.rows.values())
    assert not any(row["operation"]["S"].endswith(".outcome") for row in db.rows.values())


def test_distinct_nonowner_cognito_groups_allow_verified_access_change():
    provider = Cognito()
    provider.groups = ["TenantUsers", "SupportReadOnly"]
    client, db, provider = make(provider=provider)
    result = client.post(URL, json=body(), headers=HEADERS)
    assert result.status_code == 200, result.text
    assert provider.enabled is False
    assert verify_audit(db.rows[result.json()["intent_event_id"]], KEY)
    assert verify_audit(db.rows[result.json()["audit_event_id"]], KEY)


def test_audit_outage_fails_before_cognito_preflight_and_never_mutates():
    db = DDB(); db.fail_writes = True
    client, _, provider = make(db=db)
    assert client.post(URL, json=body(), headers=HEADERS).status_code == 503
    assert provider.calls == []


def test_provider_post_write_failure_is_indeterminate_and_never_false_success():
    provider = Cognito(); provider.fail_after = True
    client, db, provider = make(provider=provider)
    assert client.post(URL, json=body(), headers=HEADERS).status_code == 503
    assert provider.enabled is False
    assert len([r for r in db.rows.values() if r["operation"]["S"].endswith(".intent")]) == 1
    assert len([r for r in db.rows.values() if r["operation"]["S"].endswith(".outcome")]) == 0
    # The original key can never reissue another provider write.
    count = provider.calls.count("disable")
    assert client.post(URL, json=body(), headers=HEADERS).status_code == 409
    assert provider.calls.count("disable") == count
    assert provider.calls == ["list", "get", "groups", "disable", "get"]


def test_missing_verified_outcome_audit_fails_closed_without_false_success():
    db = DDB(); db.fail_outcome = True
    client, _, provider = make(db=db)
    assert client.post(URL, json=body(), headers=HEADERS).status_code == 503
    assert provider.enabled is False
    assert sum(r["operation"]["S"].endswith(".intent") for r in db.rows.values()) == 1


def test_duplicate_idempotency_key_never_issues_another_provider_write():
    client, db, provider = make()
    assert client.post(URL, json=body(), headers=HEADERS).status_code == 200
    count = provider.calls.count("disable")
    assert client.post(URL, json=body(), headers=HEADERS).status_code == 409
    assert provider.calls.count("disable") == count


def test_no_anonymous_customer_cross_origin_or_unconfigured_mutation():
    client, db, provider = make()
    assert client.post(URL, json=body()).status_code == 401
    assert client.post(URL, json=body(), headers={"X-GuardOath-Key":"owner"}).status_code == 401
    assert client.post(URL, json=body(), headers={**HEADERS, "Origin":"https://evil.example"}).status_code == 403
    assert provider.calls == []
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=lambda _: VerifiedAdmin(
        OWNER, frozenset({"owner"}), True)))
    assert TestClient(app).post(URL, json=body(), headers=HEADERS).status_code == 404


def test_modified_signed_outcome_cannot_be_verified():
    client, db, _ = make()
    outcome = client.post(URL, json=body(), headers=HEADERS).json()
    db.rows[outcome["audit_event_id"]]["new_enabled"]["S"] = "true"
    assert client.get("/v1/admin/audit/" + outcome["audit_event_id"], headers=HEADERS).status_code == 503


def test_account_action_history_recovers_signed_pair_without_provider_replay():
    client, db, provider = make()
    result = client.post(URL, json=body(), headers=HEADERS).json()
    calls = provider.calls[:]
    response = client.post(URL + "/receipt",
        json={"idempotency_key": body()["idempotency_key"]}, headers=HEADERS)
    assert response.status_code == 200, response.text
    history = response.json()
    assert history["status"] == "outcome_recorded"
    assert history["enabled_at_verification"] is False
    assert history["current_provider_state_verified"] is False
    assert history["intent_event_id"] == result["intent_event_id"]
    assert history["outcome_event_id"] == result["audit_event_id"]
    assert history["read_audit_event_id"] in db.rows
    assert provider.calls == calls
    assert EMAIL not in response.text


def test_account_action_history_indeterminate_fails_closed_without_retry():
    provider = Cognito()
    provider.fail_after = True
    client, db, provider = make(provider=provider)
    assert client.post(URL, json=body(), headers=HEADERS).status_code == 503
    calls = provider.calls[:]
    response = client.post(URL + "/receipt",
        json={"idempotency_key": body()["idempotency_key"]}, headers=HEADERS)
    assert response.status_code == 200 and response.json()["status"] == "indeterminate"
    assert response.json()["outcome_event_id"] is None
    assert response.json()["current_provider_state_verified"] is False
    assert provider.calls == calls
    assert client.post(URL, json=body(), headers=HEADERS).status_code == 409


def test_account_action_history_owner_binding_denials_and_missing_receipts():
    client, db, provider = make()
    key = {"idempotency_key": "unknownreference00000001"}
    assert client.post(URL + "/receipt", json=key).status_code == 401
    assert client.post(URL + "/receipt", json=key, headers={
        "X-GuardOath-Key":"owner", "Origin":"https://guardoath.com"}).status_code == 401
    assert client.post(URL + "/receipt", json=key, headers={
        **HEADERS, "Origin":"https://foreign.example"}).status_code == 403
    assert client.post(URL + "/receipt", json={"idempotency_key":"short"}, headers=HEADERS).status_code == 422
    assert client.post(URL + "/receipt", json=key, headers=HEADERS).json()["status"] == "not_found"
    assert provider.calls == []
    assert client.post(URL, json=body(), headers=HEADERS).status_code == 200
    other = FastAPI()
    other.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(
            "12345678-1234-1234-1234-987654321098", frozenset({"owner"}), True)
            if token == "other" else None,
        admin_user_directory=CognitoUserDirectory(provider, POOL, KEY, clock=lambda: 100),
        read_auditor=AdminSensitiveReadAuditor(db, "audit_table", signing_key=KEY)))
    foreign = TestClient(other).post(URL + "/receipt",
        json={"idempotency_key":body()["idempotency_key"]},
        headers={**HEADERS, "X-GuardOath-Admin-Session":"other"})
    assert foreign.status_code == 200 and foreign.json()["status"] == "not_found"


def test_account_action_history_corrupt_signed_or_orphaned_outcome_denied():
    client, db, provider = make()
    ids = client.post(URL, json=body(), headers=HEADERS).json()
    db.rows[ids["audit_event_id"]]["reason"]["S"] = "Unauthorized changed reason"
    response = client.post(URL + "/receipt",
        json={"idempotency_key":body()["idempotency_key"]}, headers=HEADERS)
    assert response.status_code == 503
    assert EMAIL not in response.text
    db.rows.pop(ids["intent_event_id"])
    assert client.post(URL + "/receipt",
        json={"idempotency_key":body()["idempotency_key"]}, headers=HEADERS).status_code == 503


def test_signed_receipt_independent_cognito_read_reports_current_drift_without_mutation():
    client, db, provider = make()
    saved = client.post(URL, json=body(), headers=HEADERS).json()
    provider.calls.clear()
    request = {"idempotency_key": body()["idempotency_key"], "email": EMAIL}
    response = client.post(URL + "/receipt", json=request, headers=HEADERS)
    assert response.status_code == 200, response.text
    observed = response.json()
    assert observed["status"] == "outcome_recorded"
    assert observed["current_provider_state_verified"] is True
    assert observed["current_enabled"] is False
    assert observed["enabled_at_verification"] is False
    assert observed["current_matches_recorded_outcome"] is True
    assert observed["current_observed_at_utc"].endswith("Z")
    assert observed["intent_event_id"] == saved["intent_event_id"]
    assert provider.calls == ["list", "get"]
    assert EMAIL not in response.text and "opaque-target-id" not in response.text
    assert observed["read_audit_event_id"] in db.rows
    provider.enabled = True
    drifted = client.post(URL + "/receipt", json=request, headers=HEADERS).json()
    assert drifted["current_enabled"] is True
    assert drifted["current_matches_recorded_outcome"] is False
    assert provider.calls == ["list", "get", "list", "get"]


def test_indeterminate_receipt_live_inspection_never_claims_mutation_success():
    provider = Cognito()
    provider.fail_after = True
    client, db, provider = make(provider=provider)
    assert client.post(URL, json=body(), headers=HEADERS).status_code == 503
    provider.fail_after = False
    provider.calls.clear()
    result = client.post(URL + "/receipt", json={
        "idempotency_key": body()["idempotency_key"], "email": EMAIL}, headers=HEADERS)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "indeterminate"
    assert result.json()["current_enabled"] is False
    assert result.json()["current_provider_state_verified"] is True
    assert result.json()["current_matches_recorded_outcome"] is None
    assert result.json()["outcome_event_id"] is None
    assert provider.calls == ["list", "get"]
    assert len([row for row in db.rows.values()
                if row["operation"]["S"] == "cognito.user.status.outcome"]) == 0


def test_live_receipt_rejects_different_target_ambiguous_or_invalid_identity():
    client, db, provider = make()
    saved = client.post(URL, json=body(), headers=HEADERS).json()
    provider.calls.clear()
    for invalid in ("no-at", "different@example.com..", "two quote@example.com"):
        assert client.post(URL + "/receipt", json={
            "idempotency_key": body()["idempotency_key"], "email": invalid},
            headers=HEADERS).status_code == 422
    assert provider.calls == []
    provider.target_sub = "11223344-5566-7788-99aa-bbccddeeff00"
    response = client.post(URL + "/receipt", json={
        "idempotency_key": body()["idempotency_key"], "email": EMAIL},
        headers=HEADERS)
    assert response.status_code == 409
    assert "11223344" not in response.text
    assert provider.calls == ["list"]
    provider.target_sub = TARGET
    provider.calls.clear()
    provider.ambiguous = True
    assert client.post(URL + "/receipt", json={
        "idempotency_key": body()["idempotency_key"], "email": EMAIL},
        headers=HEADERS).status_code == 409
    assert provider.calls == ["list"]
    provider.ambiguous = False
    provider.calls.clear()
    assert client.post(URL + "/receipt", json={
        "idempotency_key": "unknownreference00000001", "email": EMAIL},
        headers=HEADERS).json()["status"] == "not_found"
    assert provider.calls == []
    assert saved["audit_event_id"] in db.rows


def test_live_receipt_never_calls_cognito_without_owner_origin_and_durable_read_audit():
    client, db, provider = make()
    client.post(URL, json=body(), headers=HEADERS)
    provider.calls.clear()
    request = {"idempotency_key": body()["idempotency_key"], "email": EMAIL}
    assert client.post(URL + "/receipt", json=request).status_code == 401
    assert client.post(URL + "/receipt", json=request,
                       headers={**HEADERS, "Origin": "https://foreign.example"}).status_code == 403
    assert provider.calls == []
    db.fail_writes = True
    assert client.post(URL + "/receipt", json=request, headers=HEADERS).status_code == 503
    assert provider.calls == []
