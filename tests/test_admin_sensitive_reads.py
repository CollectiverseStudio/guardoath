"""Signed, append-only and redacted administrative read-attempt receipts."""
from datetime import datetime, timezone
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from guardoath.admin_audit import (
    AdminSensitiveReadAuditor, AdminAuditReader, AdminAuditUnavailable, READ_REASON)
from guardoath.admin_console import VerifiedAdmin, create_admin_router

SUB = "12345678-1234-1234-1234-123456789012"
KEY = b"k" * 32


class DB:
    def __init__(self, fail=False): self.rows, self.fail = [], fail
    def put_item(self, **kw):
        self.rows.append(kw)
        if self.fail: raise RuntimeError("secret provider failure")
    def get_item(self, **kw):
        return {"Item": self.rows[-1]["Item"]} if self.rows else {}


def auditor(db):
    return AdminSensitiveReadAuditor(
        db, "admin-audit", signing_key=KEY,
        clock=lambda: datetime(2026, 11, 20, 15, tzinfo=timezone.utc))


@pytest.mark.parametrize("resource,tenant", [
    ("cognito.exact_user", "system"),
    ("stripe.subscription", "usr-good"),
    ("audit.receipt", "system"),
])
def test_persisted_read_receipt_is_signed_and_has_no_private_identity(resource, tenant):
    db = DB()
    event = auditor(db).log(actor_sub=SUB, resource=resource, tenant_id=tenant)
    assert len(event) == 64 and len(db.rows) == 1
    call = db.rows[0]
    assert call["TableName"] == "admin-audit"
    assert call["ConditionExpression"] == "attribute_not_exists(event_id)"
    assert call["Item"]["reason"] == {"S": READ_REASON}
    assert "integrity_mac" in call["Item"]
    for forbidden in ("cus_", "password", "email", "private", "bearer", "raw_request"):
        assert forbidden not in str(call).lower()
    read = AdminAuditReader(db, "admin-audit", verify_key=KEY).read(event)
    assert read["status"] == "ok" and read["integrity_status"] == "signed_verified"
    assert read["operation"] == "admin.read" and read["tenant_id"] == tenant
    assert read["change"] == {"field": "protected_read", "resource": resource}
    assert "prior_revision" not in read and "new_revision" not in read


def test_unsigned_tampered_or_revisioned_read_receipts_are_refused():
    db = DB()
    event = auditor(db).log(actor_sub=SUB, resource="cognito.exact_user")
    signed = db.rows[-1]["Item"]
    for mutated in (
        {**signed, "read_resource": {"S": "stripe.subscription"}},
        {**signed, "integrity_mac": {"S": "0" * 64}},
        {k: v for k, v in signed.items() if k != "integrity_mac"},
        {**signed, "prior_revision": {"N": "1"}, "new_revision": {"N": "2"}},
    ):
        fake = DB()
        fake.rows.append({"Item": mutated})
        with pytest.raises(AdminAuditUnavailable):
            AdminAuditReader(fake, "admin-audit", verify_key=KEY).read(event)


def test_invalid_read_context_and_database_errors_fail_closed():
    db = DB()
    for args in (
        {"actor_sub": "short", "resource": "cognito.exact_user"},
        {"actor_sub": SUB, "resource": "stripe.refund"},
        {"actor_sub": SUB, "resource": "stripe.subscription", "tenant_id": "../bad"},
    ):
        with pytest.raises(ValueError): auditor(db).log(**args)
    assert db.rows == []
    with pytest.raises(AdminAuditUnavailable) as error:
        auditor(DB(fail=True)).log(actor_sub=SUB, resource="audit.receipt")
    assert "secret provider" not in str(error.value)


def test_authenticated_sensitive_api_reads_audit_first_and_deny_on_audit_failure():
    calls = []
    class Writer:
        def __init__(self, failed): self.failed = failed
        def log(self, **kwargs):
            calls.append(("audit", kwargs))
            if self.failed: raise AdminAuditUnavailable("private audit outage")
            return "b" * 64
    class Users:
        def lookup(self, **kwargs):
            calls.append(("users", kwargs))
            return {"status": "not_found", "user": None}
    class Store:
        def get(self, tenant_id):
            calls.append(("store", tenant_id))
            return object()
    class Billing:
        def read(self, record):
            calls.append(("billing", None))
            return {"verified": False}
    class Reader:
        def read(self, event_id):
            calls.append(("receipt", None))
            return None

    def make_client(failed):
        app = FastAPI()
        app.include_router(create_admin_router(
            verify_cognito_admin=lambda token: VerifiedAdmin(SUB, frozenset({"owner"}), True),
            admin_user_directory=Users(), tenant_store=Store(), billing_reporter=Billing(),
            audit_reader=Reader(), read_auditor=Writer(failed)))
        return TestClient(app)

    headers = {"Authorization": "Bearer owner", "Origin": "https://guardoath.com"}
    c = make_client(False)
    r = c.post("/v1/admin/users/lookup", json={"email": "private@example.com"}, headers=headers)
    assert r.status_code == 200 and r.json()["read_audit_event_id"] == "b" * 64
    assert [name for name, _ in calls] == ["audit", "users"]
    assert "private@example.com" not in str(calls[0])
    calls.clear()
    r = c.get("/v1/admin/subscriptions/usr-good", headers=headers)
    assert r.status_code == 200 and r.json()["read_audit_event_id"] == "b" * 64
    assert [name for name, _ in calls] == ["audit", "store", "billing"]
    assert calls[0][1]["tenant_id"] == "usr-good"
    calls.clear()
    assert c.get("/v1/admin/audit/" + "a" * 64, headers=headers).status_code == 404
    assert [name for name, _ in calls] == ["audit", "receipt"]

    c = make_client(True)
    for method, path, opts in (
        ("post", "/v1/admin/users/lookup", {"json": {"email": "private@example.com"}}),
        ("get", "/v1/admin/subscriptions/usr-good", {}),
        ("get", "/v1/admin/audit/" + "a" * 64, {}),
    ):
        calls.clear()
        result = getattr(c, method)(path, headers=headers, **opts)
        assert result.status_code == 503 and "private" not in result.text
        assert [name for name, _ in calls] == ["audit"]
    calls.clear()
    assert c.get("/v1/admin/audit/" + "a" * 64).status_code == 401
    assert calls == []
