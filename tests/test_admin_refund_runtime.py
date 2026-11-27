"""Configured refund runtime with synthetic HTTP, Cognito and durable DynamoDB.

Production constructors, mounted admission, reference codecs and signed readers
remain real. These tests do not use a provider credential or any external I/O.
"""
import base64
import hashlib
import hmac
from time import time
from types import SimpleNamespace
import urllib.request

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from admin_refund_support import (
    ACTOR, NOW, TENANT, ProviderBoundary, invoice_reference, request, tenant,
)
from admin_test_support import AdminTestDynamo
from guardoath import admin_runtime, state_contracts
from guardoath.admin_audit import AdminSensitiveReadAuditor
from guardoath.admin_integration import (
    AdminConfigurationError, mount_admin_console,
)
from guardoath.admin_invoice_refund import AdminInvoiceRefund, RefundUnavailable
from guardoath.admin_invoices import InvoiceRefundReferences
from guardoath.admin_refund_provider import StripeInvoiceRefund
from guardoath.admin_refund_state import DynamoRefundState
from guardoath.dynamodb_store import DynamoDBTenantStore, to_item
from test_admin_runtime import AdminSessions, Cognito, Tenants, configure


READ_KEY = "sk_test_synthetic_runtime_read_0123456789"
CANCEL_KEY = "rk_test_synthetic_runtime_cancel_0123456789"
REFUND_KEY = "rk_test_synthetic_runtime_refund_0123456789"
CURSOR_HEX = "ab" * 32
PREFIX = "/v1/admin/subscriptions/" + TENANT + "/refund"
HEADERS = {"X-GuardOath-Admin-Session": "valid-owner", "Origin": "https://guardoath.com"}


class OwnerSessions(AdminSessions):
    def verified_claims(self, token):
        if token == "valid-owner":
            return {"sub": ACTOR, "username": "operator", "amr": ["pwd", "mfa"],
                    "auth_time": int(time())}
        return None


class OwnerCognito(Cognito):
    def __init__(self):
        self.calls = []

    def get_user(self, **kwargs):
        self.calls.append("get_user")
        return {"Username": "operator"}

    def admin_get_user(self, **kwargs):
        self.calls.append("admin_get_user")
        return {"Enabled": True, "UserStatus": "CONFIRMED",
                "UserAttributes": [{"Name": "sub", "Value": ACTOR}]}

    def admin_list_groups_for_user(self, **kwargs):
        self.calls.append("admin_list_groups_for_user")
        return {"Groups": [{"GroupName": "GuardOathOwners"}]}


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    configure(monkeypatch)
    for name, value in {
        "GUARDOATH_ADMIN_NATIVE_MFA_ENABLED": "0",
        "GUARDOATH_ADMIN_ENROLLMENT_SUBJECT": "",
        "GUARDOATH_ADMIN_AUDIT_TABLE": "audit_table",
        "GUARDOATH_STRIPE_SECRET_KEY": READ_KEY,
        "GUARDOATH_STRIPE_PRICE_ID": "price_plan",
        "GUARDOATH_PAID_MONTHLY_ALLOWANCE": "",
        "GUARDOATH_ADMIN_STRIPE_WRITE_KEY": "",
        "GUARDOATH_ADMIN_STRIPE_ACCOUNT_ID": "",
        "GUARDOATH_ADMIN_STRIPE_REFUND_KEY": REFUND_KEY,
        "GUARDOATH_ADMIN_STRIPE_REFUND_ACCOUNT_ID": "acct_owner",
    }.items():
        monkeypatch.setenv(name, value)

    def no_network(*_args, **_kwargs):
        pytest.fail("Refund runtime tests must not perform external I/O")

    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *_args: SimpleNamespace(open=no_network))
    monkeypatch.setattr(urllib.request, "urlopen", no_network)


@pytest.fixture
def database(monkeypatch, tmp_path):
    db = AdminTestDynamo(tmp_path / "refund-runtime.sqlite3")
    db.seed("tenant_table", to_item(tenant()))
    db.sdk_calls = []
    for name in ("get_item", "put_item", "update_item", "transact_write_items", "scan"):
        original = getattr(db, name)

        def observed(*args, _method=original, _name=name, **kwargs):
            db.sdk_calls.append(_name)
            return _method(*args, **kwargs)

        monkeypatch.setattr(db, name, observed)
    try:
        yield db
    finally:
        db.close()


def mount_runtime(monkeypatch, database, *, app=None):
    app = app if app is not None else FastAPI()
    app.state.refund_test_mount_called = False
    runtime = {}
    cognito = OwnerCognito()

    def observed_mount(actual_app, **kwargs):
        actual_app.state.refund_test_mount_called = True
        runtime.update(kwargs)
        # Preserve actual integration/router admission and all constructors.
        return mount_admin_console(actual_app, **kwargs)

    monkeypatch.setattr(admin_runtime, "mount_admin_console", observed_mount)
    assert admin_runtime.configure_admin_from_environment(app,
        tenants=Tenants(DynamoDBTenantStore("tenant_table", database)),
        admin_sessions=OwnerSessions(), cognito_client=cognito) is True
    assert not cognito.calls
    return app, runtime


def financial_rows(database):
    return [row for row in database.audit_items()
            if row.get("event_id", {}).get("S", "").startswith("financial-ir1:")]


@pytest.mark.parametrize("settings", [
    {"GUARDOATH_ADMIN_STRIPE_REFUND_KEY": ""},
    {"GUARDOATH_ADMIN_STRIPE_REFUND_ACCOUNT_ID": ""},
    {"GUARDOATH_STRIPE_SECRET_KEY": ""},
    {"GUARDOATH_STRIPE_PRICE_ID": ""},
    {"GUARDOATH_ADMIN_AUDIT_TABLE": ""},
    {"GUARDOATH_ADMIN_AUDIT_TABLE": "tenant_table"},
], ids=["missing-key", "missing-account", "missing-reader", "missing-price",
        "missing-audit", "shared-audit-table"])
def test_refund_requires_complete_independent_server_configuration(monkeypatch, database, settings):
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    app = FastAPI()
    with pytest.raises(AdminConfigurationError) as error:
        mount_runtime(monkeypatch, database, app=app)
    assert REFUND_KEY not in str(error.value) and READ_KEY not in str(error.value)
    assert app.state.refund_test_mount_called is False
    assert not database.sdk_calls and not database.audit_items()


@pytest.mark.parametrize("key,account,reader", [
    ("pk_test_synthetic_runtime_0123456789", "acct_owner", READ_KEY),
    ("rk_test_short", "acct_owner", READ_KEY),
    (REFUND_KEY + "\n", "acct_owner", READ_KEY),
    ("rk_live_synthetic_runtime_0123456789", "acct_owner", READ_KEY),
    (REFUND_KEY, "acct_owner", "sk_live_synthetic_runtime_read_0123456789"),
    (REFUND_KEY, "acct_bad/path", READ_KEY),
    (REFUND_KEY, "acct_", READ_KEY),
    (REFUND_KEY, "cus_owner", READ_KEY),
], ids=["publishable-key", "short-key", "key-whitespace", "live-key-test-reader",
        "test-key-live-reader", "account-path", "empty-account-id", "wrong-account-type"])
def test_invalid_refund_key_account_or_mode_fails_before_mount(
        monkeypatch, database, key, account, reader):
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_REFUND_KEY", key)
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_REFUND_ACCOUNT_ID", account)
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", reader)
    app = FastAPI()
    with pytest.raises(AdminConfigurationError, match="Refund credential/account") as error:
        mount_runtime(monkeypatch, database, app=app)
    assert key not in str(error.value)
    assert app.state.refund_test_mount_called is False
    assert not database.sdk_calls and not database.audit_items()


@pytest.mark.parametrize("reused", ["reader", "cancellation"])
def test_refund_cannot_reuse_an_existing_provider_credential(monkeypatch, database, reused):
    if reused == "reader":
        monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_REFUND_KEY", READ_KEY)
    else:
        monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_WRITE_KEY", REFUND_KEY)
        monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_ACCOUNT_ID", "acct_owner")
    with pytest.raises(AdminConfigurationError, match="dedicated credential"):
        mount_runtime(monkeypatch, database)
    assert not database.sdk_calls and not database.audit_items()


@pytest.mark.parametrize("mode", ["test", "live"])
def test_runtime_binds_real_refund_service_codec_and_auditor_without_io(monkeypatch, database, mode):
    monkeypatch.setenv("GUARDOATH_STRIPE_SECRET_KEY", READ_KEY.replace("_test_", "_" + mode + "_"))
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_WRITE_KEY", CANCEL_KEY.replace("_test_", "_" + mode + "_"))
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_ACCOUNT_ID", "acct_owner")
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_REFUND_KEY", REFUND_KEY.replace("_test_", "_" + mode + "_"))
    _, runtime = mount_runtime(monkeypatch, database)
    actions = runtime["billing_refunds"]
    assert isinstance(actions, AdminInvoiceRefund)
    assert isinstance(actions.state, DynamoRefundState)
    assert isinstance(actions.provider, StripeInvoiceRefund)
    assert isinstance(actions.invoice_references, InvoiceRefundReferences)
    assert isinstance(runtime["read_auditor"], AdminSensitiveReadAuditor)
    assert actions.state.client is database
    assert actions.tenant_table == "tenant_table" and actions.state.table_name == "audit_table"
    assert actions.provider.expected_account_id == "acct_owner"
    assert actions.provider.expected_price_id == "price_plan"
    assert actions.provider.expected_livemode is (mode == "live")
    assert actions.provider is not runtime["billing_cancellation"].provider
    assert actions.invoice_references is runtime["billing_invoices"].refund_references
    assert actions.invoice_references.expected_livemode is (mode == "live")
    auditor = runtime["read_auditor"]
    assert auditor.ddb is database and auditor.audit_table == actions.state.table_name
    audit_key = hmac.new(bytes.fromhex(CURSOR_HEX), b"guardoath-admin-audit-v1", hashlib.sha256).digest()
    assert actions.state.audit_key == auditor.signing_key == audit_key
    assert runtime["require_persistent_read_audit"] is True
    assert runtime["billing_configuration"] == {
        "signed_audit": True, "stripe_read": True, "plan_allowance": False,
        "cancellation_provider": True, "refund_provider": True,
    }
    # A separate codec constructed from the configured cursor key can reopen
    # a reference emitted by the runtime invoice-history codec.
    token = actions.invoice_references.seal(tenant(), actor=ACTOR, invoice_id="in_refund", now=NOW)
    codec = InvoiceRefundReferences(bytes.fromhex(CURSOR_HEX),
        expected_price_id="price_plan", expected_livemode=mode == "live")
    assert codec.open(token, tenant(), actor=ACTOR, now=NOW) == "in_refund"
    assert not database.sdk_calls and not database.audit_items()


def test_explicit_refund_reader_keeps_other_writers_and_admits_only_preview_and_receipts(
        monkeypatch, database):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", False)
    assert state_contracts.new_refund_state_enabled() is False
    assert state_contracts.new_subscription_state_enabled() is True
    assert state_contracts.new_cancellation_state_enabled() is True
    assert state_contracts.new_catalog_publication_state_enabled() is True
    assert state_contracts.new_catalog_publication_lifecycle_state_enabled() is True
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_WRITE_KEY", CANCEL_KEY)
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_ACCOUNT_ID", "acct_owner")
    monkeypatch.setenv("GUARDOATH_ENABLE_NEW_REFUND_STATE", "1")
    app, runtime = mount_runtime(monkeypatch, database)
    assert not database.sdk_calls
    client = TestClient(app)
    assert client.post(PREFIX + "/preview", headers=HEADERS, json={}).status_code == 422
    assert client.post(PREFIX, headers=HEADERS, json={}).status_code == 404
    assert client.post(PREFIX + "/refresh", headers=HEADERS, json={}).status_code == 404
    assert client.post("/v1/admin/subscriptions/" + TENANT + "/cancel-at-period-end",
                       headers=HEADERS, json={}).status_code == 422
    receipt = client.post(PREFIX + "/receipt", headers=HEADERS,
        json={"idempotency_key": "refund-runtime-123456789"})
    assert receipt.status_code == 200 and receipt.json()["refund"]["status"] == "not_found"
    features_response = client.get("/v1/admin/features", headers=HEADERS)
    assert features_response.status_code == 200, features_response.text
    features = features_response.json()
    assert features["billing_refund_preview"] is True
    assert features["billing_refund_receipts"] is True
    assert features["billing_refund_submit"] is False
    assert features["billing_refund_refresh"] is False
    assert features["billing_cancellation_schedule"] is True
    assert features["billing_configuration"]["refund_provider"] is True
    assert features["billing_configuration"]["refund_writer_enabled"] is False
    before = list(database.sdk_calls)
    with pytest.raises(RefundUnavailable):
        runtime["billing_refunds"].apply(actor=ACTOR, tenant_id=TENANT,
            expected_revision=3, preview_reference="not-a-preview", confirmation="REFUND",
            reason="Owner requested full refund", idempotency_key="refund-runtime-123456789")
    with pytest.raises(RefundUnavailable):
        runtime["billing_refunds"].refresh(actor=ACTOR, tenant_id=TENANT,
            idempotency_key="refund-runtime-123456789")
    assert database.sdk_calls == before and not financial_rows(database)


@pytest.mark.parametrize("writer", [False, True])
def test_missing_refund_pair_retains_reader_and_never_admits_provider_actions(
        monkeypatch, database, writer):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", writer)
    monkeypatch.delenv("GUARDOATH_ADMIN_STRIPE_REFUND_KEY")
    monkeypatch.delenv("GUARDOATH_ADMIN_STRIPE_REFUND_ACCOUNT_ID")
    app, runtime = mount_runtime(monkeypatch, database)
    assert runtime["billing_refunds"].provider is None
    assert runtime["billing_refunds"].invoice_references is runtime["billing_invoices"].refund_references
    assert runtime["billing_configuration"]["refund_provider"] is False
    assert not database.sdk_calls
    client = TestClient(app)
    assert client.post(PREFIX, headers=HEADERS, json={}).status_code == 404
    assert client.post(PREFIX + "/refresh", headers=HEADERS, json={}).status_code == 404
    receipt = client.post(PREFIX + "/receipt", headers=HEADERS,
        json={"idempotency_key": "refund-runtime-123456789"})
    assert receipt.status_code == 200 and receipt.json()["refund"]["status"] == "not_found"
    response = client.get("/v1/admin/features", headers=HEADERS)
    assert response.status_code == 200, response.text
    features = response.json()
    assert features["billing_refund_receipts"] is True
    assert all(features[name] is False for name in
               ("billing_refund_preview", "billing_refund_submit", "billing_refund_refresh"))
    assert not financial_rows(database)


@pytest.mark.parametrize("lost_response", [False, True], ids=["verified", "indeterminate"])
@pytest.mark.parametrize("retain_invoice_reader", [False, True], ids=["no-stripe", "invoice-reader"])
def test_source_default_runtime_refund_and_reopened_receipt_survive_credential_removal(
        monkeypatch, database, tmp_path, lost_response, retain_invoice_reader):
    # Exercise the untouched source writer with synthetic external boundaries;
    # the explicit compatible reader below must preserve the exact receipt.
    assert state_contracts.new_refund_state_enabled() is True
    boundary = ProviderBoundary()
    boundary.lose_post_response = lost_response

    def transport(http_request, *, timeout):
        headers = {name.lower(): value for name, value in http_request.header_items()}
        credential = base64.b64decode(headers["authorization"][6:]).decode("ascii")
        assert credential == REFUND_KEY + ":"
        assert headers["stripe-version"] == "2025-02-24.acacia"
        return boundary.open(http_request, timeout=timeout)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_args: SimpleNamespace(open=transport))
    app, runtime = mount_runtime(monkeypatch, database)
    assert not database.sdk_calls and not boundary.calls
    actions = runtime["billing_refunds"]
    actions._clock = lambda: NOW
    before = database.get_item(TableName="tenant_table", Key={"tenant_id": {"S": TENANT}})
    client = TestClient(app)
    features_response = client.get("/v1/admin/features", headers=HEADERS)
    assert features_response.status_code == 200, features_response.text
    features = features_response.json()
    assert all(features[name] is True for name in (
        "billing_refund_preview", "billing_refund_submit", "billing_refund_receipts", "billing_refund_refresh"))
    assert features["billing_configuration"]["refund_writer_enabled"] is True
    reviewed = client.post(PREFIX + "/preview", headers=HEADERS,
        json={"expected_revision": 3, "invoice_reference": invoice_reference(actions)})
    assert reviewed.status_code == 200, reviewed.text
    preview = reviewed.json()["refund"]
    assert preview["refund_available"] is True
    command = {name: value for name, value in request(preview).items()
               if name not in ("actor", "tenant_id")}
    submitted = client.post(PREFIX, headers=HEADERS, json=command)
    assert submitted.status_code == 200, submitted.text
    expected = submitted.json()["refund"]
    assert expected["status"] == ("indeterminate" if lost_response else "verified")
    assert expected["integrity_status"] == "signed_verified"
    assert expected["tenant_mutated"] is False and len(boundary.posts) == 1
    intent, outcome = actions.state.history(expected["intent"]["action_id"])
    assert outcome["status"] == expected["status"] and intent["amount_minor"] == 1299
    post_headers = {name.lower(): value for name, value in boundary.posts[0].header_items()}
    assert post_headers["idempotency-key"] == "guardoath-ir1-" + intent["action_id"]
    count = len(boundary.calls)
    replay = client.post(PREFIX, headers=HEADERS, json=command)
    assert replay.status_code == 200 and replay.json()["refund"] == expected
    assert len(boundary.calls) == count and len(boundary.posts) == 1
    assert database.get_item(TableName="tenant_table", Key={"tenant_id": {"S": TENANT}}) == before
    retained_rows = financial_rows(database)

    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", False)
    monkeypatch.delenv("GUARDOATH_ADMIN_STRIPE_REFUND_KEY")
    monkeypatch.delenv("GUARDOATH_ADMIN_STRIPE_REFUND_ACCOUNT_ID")
    if not retain_invoice_reader:
        monkeypatch.delenv("GUARDOATH_STRIPE_SECRET_KEY")
        monkeypatch.delenv("GUARDOATH_STRIPE_PRICE_ID")

    # Reopen the actual persisted typed rows through a fresh runtime/client;
    # no preconstructed refund service or reader is injected during recovery.
    reopened = AdminTestDynamo(tmp_path / "refund-runtime.sqlite3")
    try:
        restored_app, restored = mount_runtime(monkeypatch, reopened)
        reader = restored["billing_refunds"]
        assert reader is not actions and reader.state.client is reopened
        assert reader.provider is None and reader.state.audit_key == actions.state.audit_key
        assert (reader.invoice_references is not None) is retain_invoice_reader
        assert (restored["billing_reporter"] is not None) is retain_invoice_reader
        assert restored["billing_configuration"]["refund_provider"] is False
        restored_client = TestClient(restored_app)
        assert restored_client.post(PREFIX, headers=HEADERS, json={}).status_code == 404
        assert restored_client.post(PREFIX + "/refresh", headers=HEADERS, json={}).status_code == 404
        assert reader.receipt(actor=ACTOR, tenant_id=TENANT,
            idempotency_key=command["idempotency_key"]) == expected
        historical = restored_client.post(PREFIX + "/receipt", headers=HEADERS,
            json={"idempotency_key": command["idempotency_key"]})
        assert historical.status_code == 200 and historical.json()["refund"] == expected
        audit = restored["audit_reader"].read(historical.json()["read_audit_event_id"])
        assert audit["integrity_status"] == "signed_verified"
        assert audit["change"] == {"field": "protected_read", "resource": "audit.receipt"}
        assert financial_rows(reopened) == retained_rows
        assert reopened.transactions == 0 and len(boundary.calls) == count
        assert reopened.get_item(TableName="tenant_table", Key={"tenant_id": {"S": TENANT}}) == before
    finally:
        reopened.close()


@pytest.mark.parametrize("issuer", ["preview", "test-key", "reused", "live-key"])
def test_open_invoice_credit_runtime_requires_a_dedicated_test_key(monkeypatch, database, issuer):
    key = {"preview": "", "test-key": "rk_test_dedicated_credit_notes",
           "reused": REFUND_KEY, "live-key": "rk_live_dedicated_credit_notes"}[issuer]
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_CREDIT_KEY", key)
    monkeypatch.setenv("GUARDOATH_ADMIN_STRIPE_CREDIT_ACCOUNT_ID", "acct_owner")
    if issuer in ("reused", "live-key"):
        with pytest.raises(AdminConfigurationError):
            mount_runtime(monkeypatch, database)
        return
    app, services = mount_runtime(monkeypatch, database)
    assert services["billing_credit"].provider.can_issue is (issuer == "test-key")
    base = "/v1/admin/subscriptions/{tenant_id}/invoice-credit"
    # Use the actual runtime route table, including FastAPI's lazy routers.
    paths = set(app.openapi()["paths"])
    assert base + "/preview" in paths and base + "/receipt" in paths
    assert (base in paths) is (issuer == "test-key")
