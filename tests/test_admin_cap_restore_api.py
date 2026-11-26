"""Actual owner cap-restoration routes and provider-independent signed recovery."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from admin_cap_restore_support import ACTOR, TENANT, KEY, request, review, service
from admin_test_support import AdminTestDynamo
from guardoath import state_contracts
from guardoath.admin_actions import AdminTenantActions
from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor
from guardoath.admin_cap_restore import AdminRequestCapRestoration
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.dynamodb_store import from_item
from guardoath.tenant_record import TenantRecord

PREFIX = "/v1/admin/tenants/" + TENANT + "/allowance-cap/restore"
HEADERS = {"X-GuardOath-Admin-Session":"owner", "Origin":"https://guardoath.com"}
ENDPOINTS = ("/preview", "", "/receipt")


def client(actions, db, verifier=None):
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=verifier or (
        lambda token:VerifiedAdmin(ACTOR, frozenset({"owner"}), True) if token == "owner" else None),
        billing_cap_restore=actions,
        read_auditor=AdminSensitiveReadAuditor(db, "audit_table", signing_key=KEY),
        require_persistent_read_audit=True))
    return TestClient(app)


def payload(endpoint):
    if endpoint == "/preview":
        return {"expected_revision":3}
    value = {"idempotency_key":"cap-restore-reference-123456789"}
    if endpoint == "":
        value.update(expected_revision=3, preview_reference="cr1." + "a" * 80,
            confirmation="RESTORE " + TENANT + " TO 10000 REQUESTS PER MONTH",
            reason="Restore the reviewed configured request allowance")
    return value


def submitted(preview):
    return {key:value for key,value in request(preview).items() if key not in ("actor", "tenant_id")}


def saved(db):
    return from_item(db.get_item(TableName="tenant_table", Key={"tenant_id":{"S":TENANT}},
        ConsistentRead=True)["Item"]).model_dump(mode="json")


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", True)


def test_review_restore_and_history_are_signed_and_preserve_suspension_and_policy(enabled):
    actions, db, boundary = service(status="disabled")
    browser = client(actions, db)
    before = saved(db)
    first = browser.post(PREFIX + "/preview", headers=HEADERS, json={"expected_revision":3})
    assert first.status_code == 200, first.text
    preview = first.json()["restoration"]
    assert preview["allowance_before"] == preview["operator_cap_before"] == 4000
    assert preview["plan_allowance"] == preview["allowance_after"] == 10000
    assert preview["tenant_status"] == "disabled" and preview["livemode"] is False
    body = submitted(preview)
    second = browser.post(PREFIX, headers=HEADERS, json=body)
    assert second.status_code == 200 and second.json()["restoration"]["status"] == "applied", second.text
    result = second.json()["restoration"]
    assert result["revision"] == 4 and result["operator_cap_after"] is None
    after = saved(db)
    assert after["monthly_allowance"] == 10000 and after["admin_monthly_allowance_cap"] is None
    assert after["status"] == "disabled" and after["overage"] == "block"
    changed = {"monthly_allowance", "admin_monthly_allowance_cap", "revision", "updated_at"}
    assert {key:value for key,value in after.items() if key not in changed} == {
        key:value for key,value in before.items() if key not in changed}
    count, transactions = len(boundary.calls), db.transactions
    third = browser.post(PREFIX + "/receipt", headers=HEADERS, json={"idempotency_key":body["idempotency_key"]})
    assert third.status_code == 200 and third.json()["restoration"] == {**result, "replayed":True}
    replay = browser.post(PREFIX, headers=HEADERS, json=body)
    assert replay.status_code == 200 and replay.json()["restoration"] == third.json()["restoration"]
    assert len(boundary.calls) == count == 2 and db.transactions == transactions
    reader = AdminAuditReader(db, "audit_table", verify_key=KEY)
    signed = reader.read(result["audit_event_id"])
    assert signed["integrity_status"] == "signed_verified" and signed["operation"] == "tenant.allowance_restore"
    assert signed["change"]["before"] == 4000 and signed["change"]["after"] == 10000
    for response, resource in ((first,"stripe.subscription"), (second,"stripe.subscription"),
                               (third,"audit.receipt"), (replay,"stripe.subscription")):
        assert response.headers["cache-control"] == "no-store"
        audit = reader.read(response.json()["read_audit_event_id"])
        assert audit["integrity_status"] == "signed_verified" and audit["change"]["resource"] == resource
        assert all(secret not in response.text for secret in ("cus_restore", "sub_restore", "price_plan"))


def test_historical_replay_never_removes_a_later_stricter_cap(enabled):
    actions, db, boundary = service()
    browser = client(actions, db)
    body = submitted(review(actions))
    first = browser.post(PREFIX, headers=HEADERS, json=body)
    assert first.status_code == 200
    AdminTenantActions(db, "tenant_table", "audit_table", audit_key=KEY).cap_monthly_allowance(
        tenant_id=TENANT, expected_revision=4, new_limit=2000, confirmation=TENANT,
        reason="Preserve the later restrictive request cap", actor=ACTOR,
        idempotency_key="later-cap-123456789012345", validate_record=TenantRecord.model_validate)
    before = saved(db)
    calls, transactions = len(boundary.calls), db.transactions
    for endpoint, value in (("", body), ("/receipt", {"idempotency_key":body["idempotency_key"]})):
        response = browser.post(PREFIX + endpoint, headers=HEADERS, json=value)
        assert response.status_code == 200 and response.json()["restoration"]["revision"] == 4
    assert saved(db) == before and before["monthly_allowance"] == 2000
    assert len(boundary.calls) == calls and db.transactions == transactions


def test_reader_survives_disabled_writer_missing_provider_and_unavailable_tenant(enabled, monkeypatch):
    actions, db, boundary = service()
    body = submitted(review(actions))
    result = actions.apply(actor=ACTOR, tenant_id=TENANT, **body)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", False)
    reader_only = AdminRequestCapRestoration(state=actions.state, tenant_table="tenant_table", preview_key=KEY)
    browser = client(reader_only, db)
    old_get = db.get_item
    def audit_only(**kwargs):
        assert kwargs["TableName"] == "audit_table"
        return old_get(**kwargs)
    monkeypatch.setattr(db, "get_item", audit_only)
    calls = len(boundary.calls)
    response = browser.post(PREFIX + "/receipt", headers=HEADERS, json={"idempotency_key":body["idempotency_key"]})
    assert response.status_code == 200 and response.json()["restoration"] == {**result, "replayed":True}
    assert len(boundary.calls) == calls
    flags = browser.get("/v1/admin/features", headers=HEADERS).json()
    assert flags["billing_cap_restore_receipts"] is True
    assert flags["billing_cap_restore_preview"] is flags["billing_cap_restore_submit"] is False
    assert browser.post(PREFIX, headers=HEADERS, json=body).status_code == 404


def test_source_disabled_retains_review_and_missing_history_without_a_write_route(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", False)
    actions, db, boundary = service()
    browser = client(actions, db)
    before = saved(db)
    reviewed = browser.post(PREFIX + "/preview", headers=HEADERS, json={"expected_revision":3})
    assert reviewed.status_code == 200 and reviewed.json()["restoration"]["restore_available"] is False
    assert browser.post(PREFIX, headers=HEADERS, json=submitted(reviewed.json()["restoration"])).status_code == 404
    history = browser.post(PREFIX + "/receipt", headers=HEADERS, json=payload("/receipt"))
    assert history.status_code == 200 and history.json()["restoration"]["status"] == "not_found"
    assert saved(db) == before and db.transactions == 0 and len(boundary.calls) == 1


@pytest.mark.parametrize("role,mfa", [("customer",True), ("billing_admin",True), ("support",True), ("owner",False)])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_only_verified_owner_mfa_can_reach_restoration_boundaries(enabled, role, mfa, endpoint):
    actions, db, boundary = service()
    response = client(actions, db, lambda _:VerifiedAdmin(ACTOR, frozenset({role}), mfa)).post(
        PREFIX + endpoint, headers=HEADERS, json=payload(endpoint))
    assert response.status_code == 403 and not boundary.calls and not db.audit_items()


@pytest.mark.parametrize("headers", [{}, {"X-GuardOath-Key":"customer-key"}, {"X-GuardOath-Admin-Session":"forged"}])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_missing_verified_identity_never_reaches_restoration(enabled, headers, endpoint):
    actions, db, boundary = service()
    response = client(actions, db).post(PREFIX + endpoint,
        headers={**headers, "Origin":HEADERS["Origin"]}, json=payload(endpoint))
    assert response.status_code == 401 and not boundary.calls and not db.audit_items()


@pytest.mark.parametrize("origin", [None, "https://foreign.invalid", "http://localhost:8780"])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_restoration_posts_require_same_origin_before_reads(enabled, origin, endpoint):
    actions, db, boundary = service()
    headers = {key:value for key,value in HEADERS.items() if key != "Origin"}
    if origin is not None:
        headers["Origin"] = origin
    response = client(actions, db).post(PREFIX + endpoint, headers=headers, json=payload(endpoint))
    assert response.status_code == 403 and not boundary.calls and not db.audit_items()


@pytest.mark.parametrize("extra", [{"new_limit":10000}, {"plan_monthly_allowance":10000},
    {"subscription_id":"sub_other"}, {"livemode":False}, {"actor":ACTOR}])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_no_client_authority_for_restored_allowance_provider_or_actor(enabled, extra, endpoint):
    actions, db, boundary = service()
    response = client(actions, db).post(PREFIX + endpoint, headers=HEADERS, json={**payload(endpoint), **extra})
    assert response.status_code == 422 and not boundary.calls and not db.audit_items()


@pytest.mark.parametrize("revision", [True, "3", 0, (1 << 53) - 1])
def test_review_revision_is_strict_and_can_advance_safely(enabled, revision):
    actions, db, boundary = service()
    response = client(actions, db).post(PREFIX + "/preview", headers=HEADERS, json={"expected_revision":revision})
    assert response.status_code == 422 and not boundary.calls and not db.audit_items()


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_audit_outage_precedes_tenant_and_provider_reads(enabled, endpoint, monkeypatch):
    actions, db, boundary = service()
    db.fail_audit = True
    reads = []
    monkeypatch.setattr(db, "get_item", lambda **kwargs:reads.append(kwargs))
    response = client(actions, db).post(PREFIX + endpoint, headers=HEADERS, json=payload(endpoint))
    assert response.status_code == 503 and not reads and not boundary.calls


def test_wrong_confirmation_and_provider_failure_are_private_safe(enabled):
    actions, db, boundary = service()
    browser = client(actions, db)
    body = submitted(review(actions))
    count = len(boundary.calls)
    denied = browser.post(PREFIX, headers=HEADERS, json={**body,
        "confirmation":"RESTORE " + TENANT + " TO 20000 REQUESTS PER MONTH"})
    assert denied.status_code in (409,422) and len(boundary.calls) == count and db.transactions == 0
    boundary.before = lambda _:(_ for _ in ()).throw(RuntimeError("rk_live_private cus_private provider details"))
    response = browser.post(PREFIX + "/preview", headers=HEADERS, json={"expected_revision":3})
    assert response.status_code == 503 and "private" not in response.text and "rk_" not in response.text


@pytest.mark.parametrize("mismatch", ["client", "table", "key"])
def test_restoration_router_requires_its_persistent_audit_authority(enabled, mismatch):
    actions, db, _ = service()
    auditor = AdminSensitiveReadAuditor(AdminTestDynamo() if mismatch == "client" else db,
        "other_audit" if mismatch == "table" else "audit_table",
        signing_key=b"other-restoration-key-01234567890123456789" if mismatch == "key" else KEY)
    with pytest.raises(ValueError, match="bound persistent"):
        create_admin_router(billing_cap_restore=actions, read_auditor=auditor)
