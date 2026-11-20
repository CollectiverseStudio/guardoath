"""Owner overview and capabilities require signed audit attempts when configured."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from guardoath.admin_console import VerifiedAdmin, create_admin_router

OWNER = "12345678-1234-1234-1234-123456789012"
EVENT = "d" * 64


class Audit:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def log(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("private DynamoDB details")
        return EVENT


def client(audit=None):
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(
            OWNER, frozenset({"owner"}), True) if token == "owner" else None,
        read_auditor=audit))
    return TestClient(app)


def test_each_owner_metadata_read_has_distinct_fixed_resource_and_receipt():
    audit = Audit()
    c = client(audit)
    for path, resource in (
        ("/v1/admin/overview", "admin.overview"),
        ("/v1/admin/features", "admin.features"),
    ):
        response = c.get(path, headers={"Authorization": "Bearer owner"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["read_audit_event_id"] == EVENT
        assert audit.calls[-1] == {
            "actor_sub": OWNER, "resource": resource, "tenant_id": "system"}
        assert not any("token" in key or "email" in key for key in audit.calls[-1])
    assert len(audit.calls) == 2
    assert c.get("/v1/admin/features", headers={"Authorization":"Bearer owner"}).json()[
        "sensitive_read_audit"] is True


def test_audit_failure_fails_closed_and_never_exposes_metadata():
    audit = Audit(fail=True)
    c = client(audit)
    for path in ("/v1/admin/overview", "/v1/admin/features"):
        response = c.get(path, headers={"Authorization": "Bearer owner"})
        assert response.status_code == 503
        assert "private" not in response.text
        assert "metrics" not in response.text and "tenant_status_changes" not in response.text
    assert len(audit.calls) == 2


def test_anonymous_and_customer_tokens_cannot_create_read_receipts():
    audit = Audit()
    c = client(audit)
    for path in ("/v1/admin/overview", "/v1/admin/features"):
        assert c.get(path).status_code == 401
        assert c.get(path, headers={"Authorization":"Bearer customer"}).status_code == 401
    assert audit.calls == []


def test_unconfigured_audit_is_disclosed_and_does_not_fabricate_metric_values():
    c = client()
    overview = c.get("/v1/admin/overview", headers={"Authorization": "Bearer owner"})
    features = c.get("/v1/admin/features", headers={"Authorization": "Bearer owner"})
    assert overview.status_code == features.status_code == 200
    assert "read_audit_event_id" not in overview.json()
    assert "read_audit_event_id" not in features.json()
    assert features.json()["sensitive_read_audit"] is False
    for metric in overview.json()["metrics"].values():
        assert metric["value"] is None and metric["status"] == "unavailable"


def test_enforced_production_mode_never_returns_unaudited_owner_metadata():
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(
            OWNER, frozenset({"owner"}), True) if token == "owner" else None,
        require_persistent_read_audit=True))
    c = TestClient(app)
    for endpoint in ("/v1/admin/overview", "/v1/admin/features"):
        assert c.get(endpoint).status_code == 401
        response = c.get(endpoint, headers={"Authorization": "Bearer owner"})
        assert response.status_code == 503
        assert "metrics" not in response.text
        assert "tenant_status_changes" not in response.text
        assert "read_audit_event_id" not in response.text


def test_enforced_production_mode_requires_real_receipt_before_owner_metadata():
    audit = Audit()
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(
            OWNER, frozenset({"owner"}), True) if token == "owner" else None,
        read_auditor=audit, require_persistent_read_audit=True))
    c = TestClient(app)
    result = c.get("/v1/admin/overview", headers={"Authorization": "Bearer owner"})
    assert result.status_code == 200
    assert result.json()["read_audit_event_id"] == EVENT
    assert audit.calls == [{"actor_sub": OWNER, "resource": "admin.overview", "tenant_id": "system"}]


def test_invalid_enforcement_flag_is_not_implicitly_truthy():
    import pytest
    with pytest.raises(ValueError, match="explicit boolean"):
        create_admin_router(require_persistent_read_audit="false")
