"""Production-shaped adapter tests: no fake admin mode or full-table scans."""
from dataclasses import dataclass
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guardoath.admin_integration import AdminConfigurationError, mount_admin_console

SUB = "12345678-1234-1234-1234-123456789012"

class Sessions:
    pool_id = "us-east-1_example"
    def verified_claims(self, token):
        if token != "signed-owner":
            return None
        return {"sub": SUB, "username": "operator", "amr": ["pwd", "mfa"]}

class Cognito:
    def get_user(self, *, AccessToken):
        return {"Username": "operator"}
    def admin_get_user(self, *, UserPoolId, Username):
        return {"Enabled": True, "UserStatus": "CONFIRMED", "UserAttributes": [{"Name":"sub", "Value":SUB}]}
    def admin_list_groups_for_user(self, *, UserPoolId, Username, Limit):
        return {"Groups": [{"GroupName":"GuardOathOwners"}]}

class DDB:
    def __init__(self): self.calls=[]
    def scan(self, **kwargs):
        self.calls.append(kwargs)
        return {"Items": [{"tenant_id":{"S":"demo-1"}, "revision":{"N":"2"},
            "record":{"S":'{"tenant_id":"demo-1","display_name":"Sample","status":"active","subscription_status":"inactive","monthly_allowance":0,"key_hashes":["secret-hash"],"payer_id":"cus_private"}'}}]}

class Store:
    table_name="guard-test"
    def __init__(self): self._client=DDB()
    def get(self, tenant_id): return None
    def tenant_ids(self): raise AssertionError("unbounded enumeration must never run")

@dataclass
class Tenants:
    store: object

KEY=b"k"*32

def test_mount_uses_real_store_bounded_pages_and_owner_auth():
    app=FastAPI(); store=Store()
    mount_admin_console(app,sessions=Sessions(), tenants=Tenants(store),cognito=Cognito(),cursor_signing_key=KEY)
    client=TestClient(app)
    assert client.get("/v1/admin/tenants").status_code==401
    assert client.get("/v1/admin/tenants",headers={"Authorization":"Bearer customer-key"}).status_code==401
    response=client.get("/v1/admin/tenants?limit=5",headers={"Authorization":"Bearer signed-owner"})
    assert response.status_code==200
    assert response.json()["tenants"][0]["tenant_id"]=="demo-1"
    assert "secret-hash" not in response.text and "cus_private" not in response.text
    assert len(store._client.calls)==1 and store._client.calls[0]["Limit"]==5
    assert store._client.calls[0]["ProjectionExpression"]=="tenant_id,#record,#revision,#usage"
    # The isolated package has no production registry; the full repository does.
    # In full-repository CI it must return the actual published registry, not a
    # dummy or silent fallback.
    import importlib.util
    has_real_registry = importlib.util.find_spec("guardoath.control_registry") is not None
    catalog = client.get("/v1/admin/catalog",headers={"Authorization":"Bearer signed-owner"})
    assert catalog.status_code == (200 if has_real_registry else 503)
    if has_real_registry:
        assert catalog.json()["total_checks"] > 0

@pytest.mark.parametrize("missing", ["sessions", "tenants", "cognito", "cursor_signing_key"])
def test_missing_integrations_refuse_mount(missing):
    params=dict(sessions=Sessions(),tenants=Tenants(Store()),cognito=Cognito(),cursor_signing_key=KEY)
    params[missing]=None
    app=FastAPI()
    with pytest.raises(AdminConfigurationError): mount_admin_console(app,**params)
    assert not any(route.path=="/admin" for route in app.routes)

def test_memory_store_cannot_substitute_for_bounded_dynamodb():
    class MemoryStore:
        def tenant_ids(self): return ("demo",)
        def get(self, tenant_id): return None
    with pytest.raises(AdminConfigurationError):
        mount_admin_console(FastAPI(),sessions=Sessions(),tenants=Tenants(MemoryStore()),cognito=Cognito(),cursor_signing_key=KEY)

def test_weak_cursor_secret_and_double_mount_rejected():
    app=FastAPI();params=dict(sessions=Sessions(),tenants=Tenants(Store()),cognito=Cognito(),cursor_signing_key=KEY)
    with pytest.raises(AdminConfigurationError): mount_admin_console(app,**{**params,"cursor_signing_key":b"short"})
    mount_admin_console(app,**params)
    with pytest.raises(AdminConfigurationError): mount_admin_console(app,**params)

def test_no_admin_write_routes_are_registered():
    app=FastAPI()
    mount_admin_console(app,sessions=Sessions(),tenants=Tenants(Store()),cognito=Cognito(),cursor_signing_key=KEY)
    assert all("POST" not in route.methods and "PUT" not in route.methods and "DELETE" not in route.methods
               for route in app.routes if route.path.startswith("/v1/admin") or route.path=="/admin")


def test_browser_mount_refuses_client_mismatch_before_registering_routes():
    from guardoath.admin_browser import BrowserAuthConfig, BoundExchange
    config = BrowserAuthConfig("sample.auth.us-east-1.amazoncognito.com", "adminclient", "https://guardoath.com/admin/login")
    exchange = BoundExchange(config, sender=lambda u, b: {"access_token": "a" * 40})
    app = FastAPI()
    with pytest.raises(AdminConfigurationError, match="match token verifier"):
        mount_admin_console(app,sessions=Sessions(),tenants=Tenants(Store()),cognito=Cognito(),
                            cursor_signing_key=KEY,browser_config=config,browser_exchange=exchange)
    assert not any(route.path == "/admin" for route in app.routes)


def test_browser_mounted_with_matching_config_and_both_routes_authorized():
    from guardoath.admin_browser import BrowserAuthConfig, BoundExchange
    config = BrowserAuthConfig("sample.auth.us-east-1.amazoncognito.com", "adminclient", "https://guardoath.com/admin/login")
    exchange = BoundExchange(config, sender=lambda u, b: {"access_token": "signed-owner-token-long"})
    sessions = Sessions()
    sessions.verified_claims = lambda token: {"sub": SUB, "username": "operator", "amr": ["pwd", "mfa"]} if token == "signed-owner-token-long" else None
    sessions.client_id = config.client_id
    sessions.domain = config.domain
    app = FastAPI()
    mount_admin_console(app,sessions=sessions,tenants=Tenants(Store()),cognito=Cognito(),
                        cursor_signing_key=KEY,browser_config=config,browser_exchange=exchange)
    client = TestClient(app)
    assert client.get("/admin/login").status_code == 200
    assert client.get("/v1/admin/tenants").status_code == 401
    answer = client.post("/v1/admin/auth/exchange",json={"code":"valid", "code_verifier":"x" * 43})
    assert answer.status_code == 200
    assert answer.json()["access_token"] == "signed-owner-token-long"
    header={"Authorization": "Bearer " + answer.json()["access_token"]}
    assert client.get("/v1/admin/tenants",headers=header).status_code == 200
    assert client.get("/v1/admin/tenants",headers={"X-GuardOath-Admin-Session":answer.json()["access_token"]}).status_code == 200
    assert client.get("/v1/admin/tenants",headers={"X-GuardOath-Admin-Session":"customer-key"}).status_code == 401
    assert client.get("/v1/admin/tenants",headers={"X-GuardOath-Admin-Session":answer.json()["access_token"],
        "Authorization":"Bearer "+answer.json()["access_token"]}).status_code == 401
    assert client.get("/v1/admin/tenants",headers={"X-GuardOath-Key":answer.json()["access_token"]}).status_code == 401
    # CloudFront OAC signs the origin with AWS SigV4 and still forwards the
    # custom admin header. This combination must work in production.
    assert client.get("/v1/admin/tenants",headers={"X-GuardOath-Admin-Session":answer.json()["access_token"],
        "Authorization":"AWS4-HMAC-SHA256 Credential=cloudfront/test, SignedHeaders=host, Signature=abc"}).status_code == 200
    assert client.get("/v1/admin/tenants").status_code == 401
