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


@pytest.mark.parametrize("audit,stripe,allowance,cancellation", [
    (False, False, None, False),
    (True, False, "125", False),
    (True, True, None, False),
    (True, True, "125", False),
    (True, True, "125", True),
])
def test_financial_configuration_reports_validated_prerequisites_without_provider_reads(
        monkeypatch, audit, stripe, allowance, cancellation):
    from admin_test_support import AdminTestDynamo
    from guardoath.dynamodb_store import DynamoDBTenantStore
    from guardoath import admin_runtime
    import urllib.request

    configure(monkeypatch)
    settings = {
        "GUARDOATH_ADMIN_AUDIT_TABLE": "audit_table" if audit else "",
        "GUARDOATH_STRIPE_SECRET_KEY": "sk_test_" + "a" * 32 if stripe else "",
        "GUARDOATH_STRIPE_PRICE_ID": "price_plan" if stripe else "",
        "GUARDOATH_PAID_MONTHLY_ALLOWANCE": allowance or "",
        "GUARDOATH_ADMIN_STRIPE_WRITE_KEY": "rk_test_" + "b" * 32 if cancellation else "",
        "GUARDOATH_ADMIN_STRIPE_ACCOUNT_ID": "acct_owner" if cancellation else "",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    class NoNetwork:
        def open(self, *_args, **_kwargs):
            pytest.fail("Runtime configuration must not query or mutate Stripe")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: NoNetwork())
    mounted = []
    monkeypatch.setattr(admin_runtime, "mount_admin_console", lambda app, **kwargs: mounted.append(kwargs))
    database = AdminTestDynamo()
    try:
        configure_admin_from_environment(FastAPI(),
            tenants=Tenants(DynamoDBTenantStore("tenant_table", database)),
            admin_sessions=AdminSessions(), cognito_client=Cognito())
        assert mounted[0]["billing_configuration"] == {
            "signed_audit": audit,
            "stripe_read": stripe,
            "plan_allowance": allowance is not None,
            "cancellation_provider": cancellation,
            "refund_provider": False,
        }
        assert (mounted[0]["billing_reconciler"] is not None) is (audit and stripe and allowance is not None)
        assert (mounted[0]["billing_cancellation"] is not None) is audit
        assert database.transactions == 0 and not database.audit_items()
    finally:
        database.close()


@pytest.mark.parametrize("lost_post_response", [False, True], ids=["verified", "indeterminate"])
def test_source_default_runtime_reconciles_and_recovers_one_cancellation_without_tenant_change(
        monkeypatch, tmp_path, lost_post_response):
    """Exercise configured production constructors and transports with shipped gates.

    Only the remote DynamoDB and HTTP boundaries are synthetic. No phase flag,
    reconciler, cancellation service or receipt reader is replaced by a mock.
    """
    import base64
    import json
    import urllib.request
    from types import SimpleNamespace
    from admin_cancellation_support import ACTOR, TENANT, ProviderBoundary, Response, tenant
    from admin_test_support import AdminTestDynamo
    from guardoath import admin_runtime, state_contracts
    from guardoath.dynamodb_store import DynamoDBTenantStore, to_item
    from guardoath.tenant_record import EDITABLE_FIELDS

    assert state_contracts.new_subscription_state_enabled() is True
    assert state_contracts.new_cancellation_state_enabled() is True
    configure(monkeypatch)
    read_key = "sk_test_synthetic_runtime_read_0123456789"
    write_key = "rk_test_synthetic_runtime_cancel_0123456789"
    for key, value in {
        "GUARDOATH_ADMIN_AUDIT_TABLE": "audit_table",
        "GUARDOATH_STRIPE_SECRET_KEY": read_key,
        "GUARDOATH_STRIPE_PRICE_ID": "price_plan",
        "GUARDOATH_PAID_MONTHLY_ALLOWANCE": "10000",
        "GUARDOATH_ADMIN_STRIPE_WRITE_KEY": write_key,
        "GUARDOATH_ADMIN_STRIPE_ACCOUNT_ID": "acct_owner",
    }.items():
        monkeypatch.setenv(key, value)

    boundary = ProviderBoundary()
    now = int(time())
    boundary.value.update(current_period_start=now - 86400, current_period_end=now + 864000)
    boundary.lose_post_response = lost_post_response
    read_requests = []
    def transport(request, *, timeout):
        headers = {key.lower(): value for key, value in request.header_items()}
        assert headers["stripe-version"] == "2025-02-24.acacia"
        credential = base64.b64decode(headers["authorization"][6:]).decode("ascii")
        if credential == read_key + ":":
            assert request.get_method() == "GET" and request.data is None and timeout == 5.0
            read_requests.append(request)
            if request.full_url == "https://api.stripe.com/v1/subscriptions?customer=cus_cancel&status=all&limit=100":
                value = {"object": "list", "has_more": False, "data": [boundary.value]}
            else:
                assert request.full_url == "https://api.stripe.com/v1/subscriptions/sub_cancel"
                value = boundary.value
            return Response(json.dumps(value).encode("utf-8"))
        assert credential == write_key + ":"
        return boundary.open(request, timeout=timeout)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: SimpleNamespace(open=transport))
    mounted = []
    monkeypatch.setattr(admin_runtime, "mount_admin_console", lambda app, **kwargs: mounted.append(kwargs))
    path = tmp_path / "runtime-financial.sqlite3"
    database = AdminTestDynamo(path)
    initial = tenant().model_copy(update={"stripe_subscription_id": None, "subscription_status": "inactive"})
    database.seed("tenant_table", to_item(initial))
    store = DynamoDBTenantStore("tenant_table", database)
    try:
        configure_admin_from_environment(FastAPI(), tenants=Tenants(store),
            admin_sessions=AdminSessions(), cognito_client=Cognito())
        runtime = mounted[0]
        assert all(value for key, value in runtime["billing_configuration"].items()
                   if key != "refund_provider")
        assert runtime["billing_configuration"]["refund_provider"] is False
        assert not read_requests and not boundary.calls and database.transactions == 0

        payload = {"tenant_id": TENANT, "expected_revision": initial.revision,
            "confirmation": "RECONCILE " + TENANT,
            "reason": "Verify the saved customer subscription", "actor": ACTOR,
            "idempotency_key": "runtime-reconcile-123456789"}
        result = runtime["billing_reconciler"].apply(**payload)
        saved = store.get(TENANT)
        assert result["subscription_status"] == "active" and result["replayed"] is False
        assert saved.stripe_subscription_id == "sub_cancel" and saved.revision == initial.revision + 1
        assert saved.stripe_plan_monthly_allowance == 10000
        assert saved.monthly_allowance == saved.admin_monthly_allowance_cap == initial.monthly_allowance == 4000
        for field in (*EDITABLE_FIELDS, "usage", "key_hashes", "status", "payer_id"):
            assert getattr(saved, field) == getattr(initial, field)
        audit = runtime["audit_reader"].read(result["audit_event_id"])
        assert audit["integrity_status"] == "signed_verified" and audit["operation"] == "billing.reconcile"
        assert len(read_requests) == 2 and not boundary.calls and database.transactions == 1
        assert runtime["billing_reconciler"].apply(**payload) == {**result, "replayed": True}
        assert len(read_requests) == 2 and database.transactions == 1

        cancellation = runtime["billing_cancellation"]
        prepared = cancellation.preview(actor=ACTOR, tenant_id=TENANT, expected_revision=saved.revision)
        assert prepared["scheduling_available"] is True
        command = {"actor": ACTOR, "tenant_id": TENANT, "expected_revision": saved.revision,
            "preview_reference": prepared["preview_reference"], "confirmation": prepared["confirmation"],
            "reason": "Owner requested cancellation at the verified period end",
            "idempotency_key": "runtime-cancellation-123456789"}
        scheduled = cancellation.apply(**command)
        assert scheduled["status"] == ("indeterminate" if lost_post_response else "verified")
        assert scheduled["integrity_status"] == "signed_verified"
        assert scheduled["tenant_mutated"] is False and scheduled["provider_state_refreshed"] is False
        assert store.get(TENANT) == saved and len(boundary.posts) == 1
        count = len(boundary.calls)
        assert count == (5 if lost_post_response else 7)
        assert cancellation.apply(**command) == scheduled
        assert len(boundary.calls) == count and len(read_requests) == 2
        assert database.transactions == 3

        # A fresh runtime still exposes its signed historical reader without
        # keeping the provider write credential or the plan configuration.
        for key in ("GUARDOATH_ADMIN_STRIPE_WRITE_KEY", "GUARDOATH_ADMIN_STRIPE_ACCOUNT_ID",
                    "GUARDOATH_PAID_MONTHLY_ALLOWANCE"):
            monkeypatch.delenv(key)
        reopened = AdminTestDynamo(path)
        try:
            configure_admin_from_environment(FastAPI(),
                tenants=Tenants(DynamoDBTenantStore("tenant_table", reopened)),
                admin_sessions=AdminSessions(), cognito_client=Cognito())
            recovery = mounted[1]["billing_cancellation"]
            assert recovery.provider is None and mounted[1]["billing_reconciler"] is None
            assert mounted[1]["billing_configuration"] == {
                "signed_audit": True, "stripe_read": True,
                "plan_allowance": False, "cancellation_provider": False, "refund_provider": False}
            assert recovery.receipt(actor=ACTOR, tenant_id=TENANT,
                idempotency_key=command["idempotency_key"]) == scheduled
            assert DynamoDBTenantStore("tenant_table", reopened).get(TENANT) == saved
            assert len(boundary.calls) == count and len(read_requests) == 2
            financial = [row for row in reopened.audit_items() if "financial_operation" in row]
            assert len(financial) == 3
            for row in financial:
                _, kind, identifier = row["event_id"]["S"].split(":")
                recovery.state.verify_item(row, kind, identifier)
        finally:
            reopened.close()
    finally:
        database.close()
