"""Runtime opt-in and safety contracts without external AWS or Cognito calls."""
from dataclasses import dataclass
from time import time

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


def test_hosted_runtime_denies_admin_data_without_persistent_audit_table(monkeypatch):
    configure(monkeypatch)
    monkeypatch.delenv("GUARDOATH_ADMIN_AUDIT_TABLE", raising=False)

    class OwnerSessions(AdminSessions):
        def verified_claims(self, token):
            if token != "valid-owner":
                return None
            return {"sub": "12345678-1234-1234-1234-123456789012",
                    "username": "operator", "amr": ["pwd", "mfa"],
                    "auth_time": int(time())}

    class OwnerCognito(Cognito):
        def get_user(self, **kwargs):
            return {"Username": "operator"}

        def admin_get_user(self, **kwargs):
            return {"Enabled": True, "UserStatus": "CONFIRMED",
                    "UserAttributes": [{"Name": "sub",
                       "Value": "12345678-1234-1234-1234-123456789012"}]}

        def admin_list_groups_for_user(self, **kwargs):
            return {"Groups": [{"GroupName": "GuardOathOwners"}]}

    class NoTenantReads:
        def scan(self, **kwargs):
            raise AssertionError("Owner tenant data was accessed without a signed audit receipt")

    class GuardedDB(DB):
        _client = NoTenantReads()

    app = FastAPI()
    assert configure_admin_from_environment(
        app, tenants=Tenants(GuardedDB()),
        admin_sessions=OwnerSessions(), cognito_client=OwnerCognito()) is True
    c = TestClient(app)
    assert c.get("/admin/login").status_code == 200
    token = {"X-GuardOath-Admin-Session": "valid-owner"}
    for endpoint in ("/v1/admin/overview", "/v1/admin/features",
                     "/v1/admin/tenants?limit=1", "/v1/admin/catalog"):
        response = c.get(endpoint, headers=token)
        assert response.status_code == 503
        assert "read_audit_event_id" not in response.text
    assert c.get("/v1/admin/overview").status_code == 401


@pytest.mark.parametrize("allowance,enabled", [(None,False),("125",True),("1000000000",True)])
@pytest.mark.parametrize("writer_phase", [False,True])
def test_owner_reconciliation_needs_explicit_server_plan_and_mounts_without_provider_calls(monkeypatch, allowance, enabled, writer_phase):
    from admin_test_support import AdminTestDynamo
    from guardoath.dynamodb_store import DynamoDBTenantStore
    from guardoath import admin_runtime, state_contracts
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_SUBSCRIPTION_STATE",writer_phase)
    configure(monkeypatch)
    monkeypatch.setenv("GUARDOATH_ADMIN_AUDIT_TABLE","audit_table")
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY","sk_test_" + "a" * 32)
    monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID","price_starter")
    if allowance is None: monkeypatch.delenv("GUARDOATH_PAID_MONTHLY_ALLOWANCE",raising=False)
    else: monkeypatch.setenv("GUARDOATH_PAID_MONTHLY_ALLOWANCE",allowance)
    mounted = []
    monkeypatch.setattr(admin_runtime,"mount_admin_console",lambda app,**kwargs:mounted.append(kwargs))
    database = AdminTestDynamo()
    try:
        assert configure_admin_from_environment(FastAPI(),tenants=Tenants(DynamoDBTenantStore("tenant_table",database)),
            admin_sessions=AdminSessions(),cognito_client=Cognito()) is True
        assert (mounted[0]["billing_reconciler"] is not None) is (enabled and writer_phase)
        assert mounted[0]["billing_reporter"] is not None and mounted[0]["billing_downgrade"] is not None
        assert database.transactions == 0 and not database.audit_items()
    finally: database.close()


@pytest.mark.parametrize("allowance", ["0","-1","true","1.5","1000000001","１２５","125 "])
def test_bad_owner_reconciliation_allowance_fails_before_mount(monkeypatch, allowance):
    from admin_test_support import AdminTestDynamo
    from guardoath.dynamodb_store import DynamoDBTenantStore
    from guardoath import admin_runtime
    configure(monkeypatch)
    monkeypatch.setenv("GUARDOATH_ADMIN_AUDIT_TABLE","audit_table")
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY","sk_test_" + "a" * 32)
    monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID","price_starter")
    monkeypatch.setenv("GUARDOATH_PAID_MONTHLY_ALLOWANCE",allowance)
    monkeypatch.setattr(admin_runtime,"mount_admin_console",lambda *a,**kw:pytest.fail("Invalid plan must not mount"))
    database = AdminTestDynamo()
    try:
        with pytest.raises(AdminConfigurationError):
            configure_admin_from_environment(FastAPI(),tenants=Tenants(DynamoDBTenantStore("tenant_table",database)),
                admin_sessions=AdminSessions(),cognito_client=Cognito())
    finally: database.close()


def test_production_invoice_service_forwards_filtered_continuation_with_stable_key(monkeypatch):
    from admin_test_support import AdminTestDynamo
    from guardoath.dynamodb_store import DynamoDBTenantStore
    from guardoath import admin_runtime, admin_invoices, stripe_provider
    from test_admin_invoice_pagination import Provider, OWNER, record
    configure(monkeypatch)
    monkeypatch.setenv("GUARDOATH_ADMIN_AUDIT_TABLE", "audit_table")
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", "sk_test_" + "a" * 32)
    monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID", "price_plan")
    monkeypatch.delenv("GUARDOATH_PAID_MONTHLY_ALLOWANCE", raising=False)
    provider = Provider()
    for row in provider.rows:
        row.update(status="open", amount_paid=0, amount_remaining=row["amount_due"])
    def subscription(binding, key):
        assert key == "sk_test_" + "a" * 32
        return provider.subscription(binding.tenant_id, binding.customer_id, binding.subscription_id)
    def invoices(binding, key, *, starting_after=None, status=None):
        assert key == "sk_test_" + "a" * 32
        return provider.fetch(binding.tenant_id, binding.customer_id, binding.subscription_id,
                              starting_after=starting_after, status=status)
    monkeypatch.setattr(stripe_provider, "fetch_subscription_snapshot", subscription)
    monkeypatch.setattr(admin_invoices, "fetch_invoice_list", invoices)
    mounted = []
    monkeypatch.setattr(admin_runtime, "mount_admin_console", lambda app, **kwargs: mounted.append(kwargs))
    database = AdminTestDynamo()
    try:
        for _ in range(2):
            assert configure_admin_from_environment(FastAPI(),
                tenants=Tenants(DynamoDBTenantStore("tenant_table", database)),
                admin_sessions=AdminSessions(), cognito_client=Cognito()) is True
        assert provider.calls == []
        first = mounted[0]["billing_invoices"].read(record(), owner_sub=OWNER, status="open")
        # A new runtime instance uses the configured key, not a process-random key.
        second = mounted[1]["billing_invoices"].read(record(), owner_sub=OWNER,
            status="open", cursor=first["next_cursor"])
        assert second["page_number"] == 2 and second["status_filter"] == "open"
        assert provider.calls[-1][4:] == ("in_private_4", "open")
        assert database.transactions == 0 and not database.audit_items()
    finally:
        database.close()

@pytest.mark.parametrize("writer_phase",[False,True])
def test_runtime_constructs_cancellation_with_exact_store_account_mode_and_independent_gate(monkeypatch,writer_phase):
    from admin_test_support import AdminTestDynamo
    from admin_cancellation_support import tenant
    from guardoath.dynamodb_store import DynamoDBTenantStore,to_item
    from guardoath import admin_runtime,state_contracts
    configure(monkeypatch)
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_CANCELLATION_STATE",writer_phase)
    monkeypatch.setenv("GUARDOATH_ADMIN_AUDIT_TABLE","audit_table")
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY","sk_test_"+"a"*32)
    monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID","price_plan")
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_WRITE_KEY","rk_test_"+"b"*32)
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_ACCOUNT_ID","acct_owner")
    monkeypatch.delenv("GUARDOATH_PAID_MONTHLY_ALLOWANCE",raising=False)
    mounted=[]
    monkeypatch.setattr(admin_runtime,"mount_admin_console",lambda app,**kwargs:mounted.append(kwargs))
    database=AdminTestDynamo()
    database.seed("tenant_table",to_item(tenant()))
    configure_admin_from_environment(FastAPI(),tenants=Tenants(DynamoDBTenantStore("tenant_table",database)),
        admin_sessions=AdminSessions(),cognito_client=Cognito())
    service=mounted[0]["billing_cancellation"]
    assert service.state.client is database and service.tenant_table=="tenant_table"
    assert service.state.table_name=="audit_table" and service.provider.account_id=="acct_owner"
    assert service.provider.price_id=="price_plan" and service.provider.livemode is False
    assert mounted[0]["billing_reconciler"] is None
    assert database.transactions==0 and not database.audit_items()


@pytest.mark.parametrize("key,account", [("rk_live_"+"b"*32,"acct_owner"),("rk_test_"+"b"*32,""),
    ("","acct_owner"),("pk_test_"+"b"*32,"acct_owner"),("rk_test_"+"b"*32,"acct_bad/path")])
def test_runtime_rejects_partial_or_wrong_mode_cancellation_configuration(monkeypatch,key,account):
    from admin_test_support import AdminTestDynamo
    from guardoath.dynamodb_store import DynamoDBTenantStore
    configure(monkeypatch)
    monkeypatch.setenv("GUARDOATH_ADMIN_AUDIT_TABLE","audit_table")
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY","sk_test_"+"a"*32)
    monkeypatch.setenv("GUARDOATH_STRIPE_PRICE_ID","price_plan")
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_WRITE_KEY",key)
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_ACCOUNT_ID",account)
    database=AdminTestDynamo()
    with pytest.raises(AdminConfigurationError):
        configure_admin_from_environment(FastAPI(),tenants=Tenants(DynamoDBTenantStore("tenant_table",database)),
            admin_sessions=AdminSessions(),cognito_client=Cognito())
    assert database.transactions==0 and not database.audit_items()
