"""Production-shaped adapter tests: no fake admin mode or full-table scans."""
from dataclasses import dataclass
from time import time
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
import pytest

def all_routes(routes):
    for route in routes:
        if getattr(route, "path", None) is not None:
            yield route
        nested = getattr(route, "routes", None)
        if nested is None:
            nested = getattr(getattr(route, "router", None), "routes", None)
        if nested is None:
            nested = getattr(getattr(route, "original_router", None), "routes", ())
        if nested:
            yield from all_routes(nested)


from guardoath.admin_integration import AdminConfigurationError, mount_admin_console

SUB = "12345678-1234-1234-1234-123456789012"

class Sessions:
    pool_id = "us-east-1_example"
    def verified_claims(self, token):
        if token != "signed-owner":
            return None
        return {"sub": SUB, "username": "operator", "amr": ["pwd", "otp", "mfa"], "auth_time": int(time())}

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
            "record":{"S":'{"tenant_id":"demo-1","display_name":"Sample","status":"active","demo":false,"subscription_status":"inactive","monthly_allowance":0,"key_hashes":["secret-hash"],"payer_id":"cus_private"}'}}]}

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
    # The fake DynamoDB source must model an explicit, authoritative customer identity.
    # A missing or string demo flag is a provider-integrity failure, not permission to mutate.
    assert response.json()["tenants"][0]["demo"] is False
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
    assert not any(route.path=="/admin" for route in all_routes(app.routes))

def test_memory_store_cannot_substitute_for_bounded_dynamodb():
    class MemoryStore:
        def tenant_ids(self): return ("demo",)
        def get(self, tenant_id): return None
    with pytest.raises(AdminConfigurationError):
        mount_admin_console(FastAPI(),sessions=Sessions(),tenants=Tenants(MemoryStore()),cognito=Cognito(),cursor_signing_key=KEY)


@pytest.mark.parametrize("mismatch", ["table", "database"])
def test_reconciliation_must_use_the_actual_mounted_tenant_store(mismatch):
    from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
    from guardoath.admin_subscription_reconcile import AdminSubscriptionReconciliation
    from guardoath.dynamodb_store import DynamoDBTenantStore
    database, other_database = AdminTestDynamo(), AdminTestDynamo()
    try:
        store = DynamoDBTenantStore("tenant_table", database)
        reconciler = AdminSubscriptionReconciliation(
            other_database if mismatch == "database" else database,
            "other_tenants" if mismatch == "table" else "tenant_table", "audit_table",
            signing_key=ADMIN_BROWSER_AUDIT_KEY, resolve_snapshot=lambda _: pytest.fail("No provider read while mounting"),
            expected_price_id="price_plan", expected_livemode=False, plan_monthly_allowance=10000)
        app = FastAPI()
        with pytest.raises(AdminConfigurationError, match="authoritative tenant store"):
            mount_admin_console(app, sessions=Sessions(), tenants=Tenants(store), cognito=Cognito(),
                cursor_signing_key=KEY, billing_reconciler=reconciler)
        assert not getattr(app.state, "guardoath_admin_mounted", False)
        assert not database.audit_items() and not other_database.audit_items()
    finally:
        database.close()
        other_database.close()


@pytest.mark.parametrize("mismatch", ["table", "database"])
def test_cap_restoration_must_use_the_actual_mounted_tenant_store(mismatch):
    from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
    from guardoath.admin_cap_restore import AdminRequestCapRestoration
    from guardoath.admin_cap_restore_state import DynamoCapRestoreState
    from guardoath.dynamodb_store import DynamoDBTenantStore
    database, other_database = AdminTestDynamo(), AdminTestDynamo()
    try:
        store = DynamoDBTenantStore("tenant_table", database)
        actions = AdminRequestCapRestoration(
            state=DynamoCapRestoreState(other_database if mismatch == "database" else database,
                "audit_table", ADMIN_BROWSER_AUDIT_KEY),
            tenant_table="other_tenants" if mismatch == "table" else "tenant_table", preview_key=KEY)
        app = FastAPI()
        with pytest.raises(AdminConfigurationError, match="authoritative tenant store"):
            mount_admin_console(app, sessions=Sessions(), tenants=Tenants(store), cognito=Cognito(),
                cursor_signing_key=KEY, billing_cap_restore=actions)
        assert not getattr(app.state, "guardoath_admin_mounted", False)
        assert not database.audit_items() and not other_database.audit_items()
    finally:
        database.close()
        other_database.close()

def test_weak_cursor_secret_and_double_mount_rejected():
    app=FastAPI();params=dict(sessions=Sessions(),tenants=Tenants(Store()),cognito=Cognito(),cursor_signing_key=KEY)
    with pytest.raises(AdminConfigurationError): mount_admin_console(app,**{**params,"cursor_signing_key":b"short"})
    mount_admin_console(app,**params)
    with pytest.raises(AdminConfigurationError): mount_admin_console(app,**params)

def test_default_mount_registers_only_the_two_protected_post_reads():
    app=FastAPI()
    mount_admin_console(app,sessions=Sessions(),tenants=Tenants(Store()),cognito=Cognito(),cursor_signing_key=KEY)
    non_get = {(route.path, method) for route in all_routes(app.routes)
               if route.path.startswith("/v1/admin") or route.path == "/admin"
               for method in route.methods - {"GET", "HEAD", "OPTIONS"}}
    assert non_get == {("/v1/admin/users/lookup", "POST"), ("/v1/admin/catalog/preview", "POST")}
    client = TestClient(app)
    assert all(client.post(path, json={}).status_code == 401 for path, _ in non_get)


def test_browser_mount_refuses_client_mismatch_before_registering_routes():
    from guardoath.admin_browser import BrowserAuthConfig, BoundExchange
    config = BrowserAuthConfig("sample.auth.us-east-1.amazoncognito.com", "adminclient", "https://guardoath.com/admin/login")
    exchange = BoundExchange(config, sender=lambda u, b: {"access_token": "a" * 40})
    app = FastAPI()
    with pytest.raises(AdminConfigurationError, match="match token verifier"):
        mount_admin_console(app,sessions=Sessions(),tenants=Tenants(Store()),cognito=Cognito(),
                            cursor_signing_key=KEY,browser_config=config,browser_exchange=exchange)
    assert not any(route.path == "/admin" for route in all_routes(app.routes))


def test_browser_mounted_with_matching_config_and_both_routes_authorized():
    from guardoath.admin_browser import BrowserAuthConfig, BoundExchange
    config = BrowserAuthConfig("sample.auth.us-east-1.amazoncognito.com", "adminclient", "https://guardoath.com/admin/login")
    exchange = BoundExchange(config, sender=lambda u, b: {"access_token": "signed-owner-token-long"})
    sessions = Sessions()
    sessions.verified_claims = lambda token: {"sub": SUB, "username": "operator", "amr": ["pwd", "mfa"], "auth_time": int(time())} if token == "signed-owner-token-long" else None
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
    # An otherwise valid owner token with a stale or absent signed auth_time
    # must be denied by both browser exchange and all authenticated APIs.
    for signed_time in (None, int(time()) - 3600):
        sessions.verified_claims = lambda token, signed_time=signed_time: {
            "sub": SUB, "username": "operator", "amr": ["pwd", "mfa"],
            **({} if signed_time is None else {"auth_time": signed_time}),
        } if token == "signed-owner-token-long" else None
        assert client.post("/v1/admin/auth/exchange",
                           json={"code": "valid", "code_verifier": "x" * 43}).status_code == 403
        assert client.get("/v1/admin/tenants",
                          headers={"X-GuardOath-Admin-Session": "signed-owner-token-long"}).status_code == 401


@pytest.mark.parametrize("reserved_path", [
    "/admin/login", "/admin/unexpected", "/v1/admin/tenants",
    "/v1/admin/tenants/{tenant_id}", "/v1/admin/auth/config", "/v1/admin",
])
def test_existing_admin_namespace_cannot_shadow_guarded_routes(reserved_path):
    app = FastAPI()
    app.add_api_route(reserved_path, lambda: {"unprotected": True}, methods=["GET"])
    before = tuple(all_routes(app.routes))
    with pytest.raises(AdminConfigurationError, match="administration route"):
        mount_admin_console(app, sessions=Sessions(), tenants=Tenants(Store()),
                            cognito=Cognito(), cursor_signing_key=KEY)
    assert tuple(all_routes(app.routes)) == before
    assert not getattr(app.state, "guardoath_admin_mounted", False)


@pytest.mark.parametrize("leaf_prefix,leaf_path,include_prefixes,reserved_path", [
    ("", "/v1/admin/refund", (), "/v1/admin/refund"),
    ("/v1/admin", "/refund", (), "/v1/admin/refund"),
    ("/admin", "/refund", ("/v1",), "/v1/admin/refund"),
    ("", "/refund", ("/admin", "/v1"), "/v1/admin/refund"),
    ("", "/login", ("/admin",), "/admin/login"),
])
def test_included_anonymous_router_refuses_guarded_mount_with_composed_prefix(
        leaf_prefix, leaf_path, include_prefixes, reserved_path):
    router = APIRouter(prefix=leaf_prefix)
    router.add_api_route(leaf_path, lambda: {"unprotected": True}, methods=["GET"])
    for prefix in include_prefixes:
        parent = APIRouter()
        parent.include_router(router, prefix=prefix)
        router = parent
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get(reserved_path).json() == {"unprotected": True}
    before = tuple(app.routes)
    store = Store()
    with pytest.raises(AdminConfigurationError, match="administration route"):
        mount_admin_console(app, sessions=Sessions(), tenants=Tenants(store),
                            cognito=Cognito(), cursor_signing_key=KEY)
    assert tuple(app.routes) == before
    assert not getattr(app.state, "guardoath_admin_mounted", False)
    assert not store._client.calls
    assert client.get("/v1/admin/features").status_code == 404


def test_asgi_mount_and_included_router_prefixes_reserve_the_effective_namespace():
    child = FastAPI()
    router = APIRouter()
    router.add_api_route("/refund", lambda: {"unprotected": True}, methods=["GET"])
    child.include_router(router, prefix="/admin")
    app = FastAPI()
    app.mount("/v1", child)
    assert TestClient(app).get("/v1/admin/refund").json() == {"unprotected": True}
    before = tuple(app.routes)
    with pytest.raises(AdminConfigurationError, match="administration route"):
        mount_admin_console(app, sessions=Sessions(), tenants=Tenants(Store()),
                            cognito=Cognito(), cursor_signing_key=KEY)
    assert tuple(app.routes) == before
    assert not getattr(app.state, "guardoath_admin_mounted", False)


@pytest.mark.parametrize("composition", ["include", "mount"])
def test_nonreserved_effective_prefix_does_not_reserve_an_unrelated_namespace(composition):
    router = APIRouter()
    router.add_api_route("/v1/admin/refund", lambda: {"public": True}, methods=["GET"])
    app = FastAPI()
    if composition == "include":
        app.include_router(router, prefix="/public")
    else:
        child = FastAPI()
        child.include_router(router)
        app.mount("/public", child)
    mount_admin_console(app, sessions=Sessions(), tenants=Tenants(Store()),
                        cognito=Cognito(), cursor_signing_key=KEY)
    client = TestClient(app)
    assert client.get("/public/v1/admin/refund").json() == {"public": True}
    assert client.get("/v1/admin/tenants").status_code == 401
    assert getattr(app.state, "guardoath_admin_mounted", False)


def test_native_mfa_challenge_client_must_match_independent_admin_token_client():
    from guardoath.admin_browser import BrowserAuthConfig, BoundExchange
    from guardoath.admin_native_mfa import CognitoNativeMfa

    class Challenges:
        def initiate_auth(self, **kw):
            raise AssertionError("No provider calls during route mounting")
        def respond_to_auth_challenge(self, **kw):
            raise AssertionError("No provider calls during route mounting")

    config = BrowserAuthConfig("sample.auth.us-east-1.amazoncognito.com",
                               "adminclient", "https://guardoath.com/admin/login")
    exchange = BoundExchange(config, sender=lambda url, body: {"access_token": "a"*40})
    sessions = Sessions()
    sessions.client_id = config.client_id
    sessions.domain = config.domain

    mismatch = CognitoNativeMfa(Challenges(), "otherclient", KEY)
    app = FastAPI()
    with pytest.raises(AdminConfigurationError, match="Native admin challenge client"):
        mount_admin_console(app, sessions=sessions, tenants=Tenants(Store()),
                            cognito=Cognito(), cursor_signing_key=KEY,
                            browser_config=config, browser_exchange=exchange,
                            native_auth=mismatch)
    assert not any(route.path == "/admin" for route in all_routes(app.routes))

    bound = CognitoNativeMfa(Challenges(), "adminclient", KEY)
    app = FastAPI()
    mount_admin_console(app, sessions=sessions, tenants=Tenants(Store()),
                        cognito=Cognito(), cursor_signing_key=KEY,
                        browser_config=config, browser_exchange=exchange,
                        native_auth=bound)
    client = TestClient(app)
    assert client.get("/v1/admin/auth/config").json()["native_mfa_enabled"] is True
    assert client.post("/v1/admin/auth/native/start",
                       json={"email":"owner@example.com","password":"private-password"},
                       headers={"Origin":"https://untrusted.example"}).status_code == 403
