"""Runtime opt-in and safety contracts without external AWS or Cognito calls."""
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guardoath.admin_integration import AdminConfigurationError
from guardoath.admin_runtime import configure_admin_from_environment

POOL = "us-east-1_Example"
DOMAIN = "operators.auth.us-east-1.amazoncognito.com"
CLIENT = "separateadminclient"


class AdminSessions:
    pool_id = POOL
    domain = DOMAIN
    client_id = CLIENT
    def verified_claims(self, token): return None


class Cognito:
    def get_user(self, **kwargs): return {}
    def admin_get_user(self, **kwargs): return {}
    def admin_list_groups_for_user(self, **kwargs): return {}


class DB:
    table_name = "tenant-table"
    _client = object()
    def get(self, tenant_id): return None


@dataclass
class Tenants:
    store: object


def configure(monkeypatch):
    for k, v in {
        "GUARDOATH_ADMIN_ENABLED": "1", "GUARDOATH_COGNITO_POOL_ID": POOL,
        "GUARDOATH_ADMIN_COGNITO_DOMAIN": DOMAIN, "GUARDOATH_ADMIN_CLIENT_ID": CLIENT,
        "GUARDOATH_ADMIN_CURSOR_HMAC_KEY": "ab" * 32,
    }.items(): monkeypatch.setenv(k, v)


def test_default_disabled_is_inert(monkeypatch):
    monkeypatch.delenv("GUARDOATH_ADMIN_ENABLED", raising=False)
    app = FastAPI()
    assert configure_admin_from_environment(app, tenants=None) is False
    assert all(not r.path.startswith("/admin") for r in app.routes)


def test_valid_config_mounts_admin_and_denies_anonymous(monkeypatch):
    configure(monkeypatch)
    app = FastAPI()
    assert configure_admin_from_environment(app, tenants=Tenants(DB()),
        admin_sessions=AdminSessions(), cognito_client=Cognito()) is True
    assert TestClient(app).get("/admin/login").status_code == 200
    entry=TestClient(app).get("/admin",follow_redirects=False)
    assert entry.status_code==303 and entry.headers["location"]=="/admin/login"
    assert TestClient(app).get("/v1/admin/tenants").status_code == 401


@pytest.mark.parametrize("mutation", [
    {"GUARDOATH_ADMIN_ENABLED": "invalid"},
    {"GUARDOATH_ADMIN_CLIENT_ID": ""},
    {"GUARDOATH_ADMIN_CURSOR_HMAC_KEY": "weak"},
    {"GUARDOATH_ADMIN_CURSOR_HMAC_KEY": "0" * 63},
    {"GUARDOATH_ADMIN_COGNITO_DOMAIN": "evil.invalid"},
])
def test_invalid_config_rejected_before_routes(monkeypatch, mutation):
    configure(monkeypatch)
    for k, v in mutation.items(): monkeypatch.setenv(k, v)
    app = FastAPI()
    with pytest.raises((AdminConfigurationError, ValueError)):
        configure_admin_from_environment(app, tenants=Tenants(DB()),
            admin_sessions=AdminSessions(), cognito_client=Cognito())
    assert not any(r.path == "/admin" for r in app.routes)


def test_customer_client_cannot_be_reused(monkeypatch):
    configure(monkeypatch)
    class Customer:
        client_id = CLIENT
    with pytest.raises(AdminConfigurationError, match="distinct admin"):
        configure_admin_from_environment(FastAPI(), tenants=Tenants(DB()),
            customer_sessions=Customer(), admin_sessions=AdminSessions(), cognito_client=Cognito())


def test_verifier_mismatch_is_refused(monkeypatch):
    configure(monkeypatch)
    class Wrong(AdminSessions):
        client_id = "otherclient"
    with pytest.raises(AdminConfigurationError, match="must match"):
        configure_admin_from_environment(FastAPI(), tenants=Tenants(DB()),
            admin_sessions=Wrong(), cognito_client=Cognito())
