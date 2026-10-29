"""Multi-tenant registry identity and isolation tests."""
import pytest

from valinator.tenants import TenantApplication, TenantRegistry


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
