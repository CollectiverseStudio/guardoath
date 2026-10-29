"""Multi-tenant registry identity and isolation tests."""
import pytest

from guardoath.tenants import TenantApplication, TenantRegistry


def test_credentials_bind_to_one_tenant_and_application():
    registry = TenantRegistry((
        TenantApplication("hospital-a", "app-a", "key-a"),
        TenantApplication("company-b", "app-b", "key-b"),
    ))
    assert registry.authenticate("key-a").tenant_id == "hospital-a"
    assert registry.authenticate("key-b").application_id == "app-b"
    assert registry.authenticate("unknown") is None
    assert registry.tenant_for_application("app-b") == "company-b"
    assert registry.tenant_for_application("missing") is None


def test_duplicate_app_or_credential_is_rejected():
    with pytest.raises(ValueError):
        TenantRegistry((TenantApplication("a", "app-a", "same"),
                        TenantApplication("b", "app-b", "same")))
    with pytest.raises(ValueError):
        TenantRegistry((TenantApplication("a", "app-a", "key-a"),
                        TenantApplication("b", "app-a", "key-b")))


def test_empty_identity_fails_closed():
    with pytest.raises(ValueError):
        TenantApplication("", "app-a", "key")
    assert TenantRegistry(()).authenticate("anything") is None


def test_tenant_registry_authenticates_api_and_rejects_cross_tenant_requests():
    from fastapi.testclient import TestClient
    from guardoath.app import create_app
    from guardoath.engine import Engine
    from guardoath.models import Policy, Rule
    from guardoath.checkers import default_checkers
    registry = TenantRegistry((
        TenantApplication("hospital-a", "app-a", "key-a"),
        TenantApplication("company-b", "app-b", "key-b"),
    ))
    engine = Engine([
        Policy(application_id="app-a", version="v1", rules=(Rule(checker_id="text_security"),)),
        Policy(application_id="app-b", version="v1", rules=(Rule(checker_id="text_security"),)),
    ], default_checkers())
    client = TestClient(create_app(engine=engine, tenant_registry=registry))
    payload = {"application_id": "app-a", "policy_version": "v1", "content": "hello"}
    assert client.post("/v1/checks/input", json=payload).status_code == 401
    assert client.post("/v1/checks/input", json=payload,
                       headers={"Authorization": "Bearer key-a"}).status_code == 200
    assert client.post("/v1/checks/input", json=payload,
                       headers={"Authorization": "Bearer key-b"}).status_code == 403
    assert client.get("/v1/validation-packs",
                      headers={"Authorization": "Bearer key-a"}).json()["application_id"] == "app-a"


def test_tenant_registry_and_legacy_credentials_are_mutually_exclusive():
    from guardoath.app import create_app
    with pytest.raises(ValueError, match="not both"):
        create_app(tenant_registry=TenantRegistry((
            TenantApplication("a", "app-a", "key-a"),)),
            credentials={"legacy-key": "demo"})
