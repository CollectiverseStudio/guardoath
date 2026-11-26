"""Configured owner runtime and direct Stripe GET with synthetic service I/O."""
import base64
import hashlib
import hmac
import json
from time import time
from types import SimpleNamespace
import urllib.request

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from admin_cap_restore_support import ACTOR, TENANT, NOW, request, snapshot, tenant
from admin_test_support import AdminTestDynamo
from guardoath import admin_runtime, state_contracts
from guardoath.admin_cap_restore import AdminRequestCapRestoration
from guardoath.admin_cap_restore_state import DynamoCapRestoreState
from guardoath.admin_integration import mount_admin_console
from guardoath.dynamodb_store import DynamoDBTenantStore, to_item
from test_admin_runtime import AdminSessions, Cognito, Tenants, configure

READ_KEY = "sk_test_synthetic_runtime_read_0123456789"
CURSOR_HEX = "ab" * 32
PREFIX = "/v1/admin/tenants/" + TENANT + "/allowance-cap/restore"
HEADERS = {"X-GuardOath-Admin-Session":"valid-owner", "Origin":"https://guardoath.com"}


class OwnerSessions(AdminSessions):
    def verified_claims(self, token):
        if token == "valid-owner":
            return {"sub":ACTOR, "username":"operator", "amr":["pwd","mfa"], "auth_time":int(time())}
        return None


class OwnerCognito(Cognito):
    def __init__(self): self.calls = []
    def get_user(self, **kwargs):
        self.calls.append("get_user")
        return {"Username":"operator"}
    def admin_get_user(self, **kwargs):
        self.calls.append("admin_get_user")
        return {"Enabled":True, "UserStatus":"CONFIRMED", "UserAttributes":[{"Name":"sub","Value":ACTOR}]}
    def admin_list_groups_for_user(self, **kwargs):
        self.calls.append("admin_list_groups_for_user")
        return {"Groups":[{"GroupName":"GuardOathOwners"}]}


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    configure(monkeypatch)
    for name, value in {
        "GUARDOATH_ADMIN_NATIVE_MFA_ENABLED":"0", "GUARDOATH_ADMIN_ENROLLMENT_SUBJECT":"",
        "GUARDOATH_ADMIN_AUDIT_TABLE":"audit_table", "GUARDOATH_STRIPE_SECRET_KEY":READ_KEY,
        "GUARDOATH_STRIPE_PRICE_ID":"price_plan", "GUARDOATH_PAID_MONTHLY_ALLOWANCE":"10000",
        "GUARDOATH_ADMIN_STRIPE_WRITE_KEY":"", "GUARDOATH_ADMIN_STRIPE_ACCOUNT_ID":"",
        "GUARDOATH_ADMIN_STRIPE_REFUND_KEY":"", "GUARDOATH_ADMIN_STRIPE_REFUND_ACCOUNT_ID":"",
    }.items(): monkeypatch.setenv(name, value)
    def no_network(*_args, **_kwargs): pytest.fail("Cap restoration runtime must remain synthetic")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_args:SimpleNamespace(open=no_network))
    monkeypatch.setattr(urllib.request, "urlopen", no_network)


@pytest.fixture
def database(monkeypatch, tmp_path):
    db = AdminTestDynamo(tmp_path / "cap-runtime.sqlite3")
    db.seed("tenant_table", to_item(tenant(status="disabled")))
    db.sdk_calls = []
    for name in ("get_item", "put_item", "update_item", "transact_write_items", "scan"):
        original = getattr(db, name)
        def observed(*args, _method=original, _name=name, **kwargs):
            db.sdk_calls.append(_name)
            return _method(*args, **kwargs)
        monkeypatch.setattr(db, name, observed)
    try: yield db
    finally: db.close()


def mount_runtime(monkeypatch, db):
    app, runtime, cognito = FastAPI(), {}, OwnerCognito()
    def actual_mount(actual_app, **kwargs):
        runtime.update(kwargs)
        return mount_admin_console(actual_app, **kwargs)
    monkeypatch.setattr(admin_runtime, "mount_admin_console", actual_mount)
    assert admin_runtime.configure_admin_from_environment(app,
        tenants=Tenants(DynamoDBTenantStore("tenant_table", db)),
        admin_sessions=OwnerSessions(), cognito_client=cognito)
    assert not cognito.calls
    runtime["billing_cap_restore"]._clock = lambda:NOW
    return app, runtime


def http_boundary(monkeypatch, *, livemode=False):
    value, calls = snapshot(), []
    value["livemode"] = livemode
    value["items"]["data"][0]["price"]["livemode"] = livemode
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size):
            assert size == 262145
            return json.dumps(value).encode()
    def open_request(req, *, timeout):
        assert req.full_url == "https://api.stripe.com/v1/subscriptions/sub_restore"
        assert req.get_method() == "GET" and req.data is None
        assert req.get_header("Stripe-version") == "2025-02-24.acacia"
        key = READ_KEY.replace("_test_", "_live_") if livemode else READ_KEY
        assert req.get_header("Authorization") == "Basic " + base64.b64encode((key+":").encode()).decode()
        assert 0 < timeout <= 5
        calls.append({"url":req.full_url, "timeout":timeout})
        return Response()
    def opener(*handlers):
        assert len(handlers) == 1
        assert handlers[0].redirect_request(None,None,302,"",{},"https://unexpected.invalid") is None
        return SimpleNamespace(open=open_request)
    monkeypatch.setattr(urllib.request, "build_opener", opener)
    return value, calls


@pytest.mark.parametrize("writer", [False,True])
@pytest.mark.parametrize("read_config,plan", [(True,True),(True,False),(False,True),(False,False)])
def test_lazy_runtime_binds_signed_history_and_exposes_only_configured_source_capabilities(
        monkeypatch, database, writer, read_config, plan):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", writer)
    if not read_config: monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", "")
    if not plan: monkeypatch.setenv("GUARDOATH_PAID_MONTHLY_ALLOWANCE", "")
    # There is no environment-controlled source activation bypass.
    monkeypatch.setenv("GUARDOATH_ENABLE_NEW_CAP_RESTORATION_STATE", "1")
    app, runtime = mount_runtime(monkeypatch, database)
    assert not database.sdk_calls
    actions = runtime["billing_cap_restore"]
    assert isinstance(actions, AdminRequestCapRestoration) and isinstance(actions.state, DynamoCapRestoreState)
    assert actions.provider_configured is (read_config and plan)
    assert actions.state.client is database and actions.tenant_table == "tenant_table"
    auditor = runtime["read_auditor"]
    expected_key = hmac.new(bytes.fromhex(CURSOR_HEX), b"guardoath-admin-audit-v1", hashlib.sha256).digest()
    assert actions.state.key == actions.state.audit_key == auditor.signing_key == expected_key
    assert actions.state.table_name == auditor.audit_table == "audit_table"
    assert runtime["require_persistent_read_audit"] is True
    client = TestClient(app)
    assert client.post(PREFIX, headers=HEADERS, json={}).status_code == (
        422 if writer and read_config and plan else 404)
    response = client.get("/v1/admin/features", headers=HEADERS)
    assert response.status_code == 200
    features = response.json()
    assert features["billing_cap_restore_preview"] is (read_config and plan)
    assert features["billing_cap_restore_submit"] is (writer and read_config and plan)
    assert features["billing_cap_restore_receipts"] is True
    assert features["billing_configuration"]["cap_restore_writer_enabled"] is writer
    assert database.transactions == 0


@pytest.mark.parametrize("livemode", [False,True])
def test_configured_full_owner_flow_uses_exact_existing_read_credential_and_preserves_suspension(
        monkeypatch, database, livemode):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", True)
    if livemode: monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", READ_KEY.replace("_test_","_live_"))
    value, calls = http_boundary(monkeypatch, livemode=livemode)
    app, runtime = mount_runtime(monkeypatch, database)
    assert not database.sdk_calls and not calls
    client = TestClient(app)
    response = client.post(PREFIX+"/preview", headers=HEADERS, json={"expected_revision":3})
    assert response.status_code == 200, response.text
    reviewed = response.json()["restoration"]
    assert reviewed["livemode"] is livemode and reviewed["tenant_status"] == "disabled"
    assert response.json()["read_audit_event_id"]
    payload = {k:v for k,v in request(reviewed).items() if k not in ("actor","tenant_id")}
    response = client.post(PREFIX, headers=HEADERS, json=payload)
    assert response.status_code == 200, response.text
    applied = response.json()["restoration"]
    assert applied["allowance_after"] == 10000 and applied["tenant_status"] == "disabled"
    current = DynamoDBTenantStore("tenant_table",database).get(TENANT)
    assert current.status == "disabled" and current.admin_monthly_allowance_cap is None
    assert current.usage == {"2026-11":231} and current.input_check_pii == "block"
    before_history = len(calls)
    response = client.post(PREFIX+"/receipt", headers=HEADERS, json={"idempotency_key":payload["idempotency_key"]})
    assert response.status_code == 200
    assert response.json()["restoration"] == {**applied,"replayed":True}
    assert before_history == len(calls) == 2 and database.transactions == 1
    assert all(private not in response.text for private in (READ_KEY,"cus_restore","sub_restore","price_plan"))


def test_new_runtime_with_provider_removed_and_source_disabled_reads_exact_historical_receipt(
        monkeypatch, database):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", True)
    _, calls = http_boundary(monkeypatch)
    app, runtime = mount_runtime(monkeypatch, database)
    actions = runtime["billing_cap_restore"]
    prepared = request(actions.preview(actor=ACTOR,tenant_id=TENANT,expected_revision=3))
    applied = actions.apply(**prepared)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CAP_RESTORATION_STATE", False)
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", "")
    monkeypatch.setenv("GUARDOATH_PAID_MONTHLY_ALLOWANCE", "")
    app, runtime = mount_runtime(monkeypatch, database)
    def no_time(): pytest.fail("Signed restoration history must survive preview expiry")
    runtime["billing_cap_restore"]._clock = no_time
    client = TestClient(app)
    response = client.post(PREFIX+"/receipt", headers=HEADERS,
                           json={"idempotency_key":prepared["idempotency_key"]})
    assert response.status_code == 200, response.text
    assert response.json()["restoration"] == {**applied,"replayed":True}
    assert runtime["billing_cap_restore"].apply(**prepared) == {**applied,"replayed":True}
    assert client.post(PREFIX,headers=HEADERS,json={}).status_code == 404
    assert len(calls) == 2 and database.transactions == 1
