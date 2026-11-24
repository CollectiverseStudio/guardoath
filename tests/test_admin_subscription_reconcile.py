"""Real reconciliation/tenant/audit integration against offline service boundaries."""
from copy import deepcopy
from datetime import datetime, timezone
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from admin_test_support import AdminTestDynamo
from guardoath.admin_actions import AdminTenantActions
from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor
from guardoath.admin_audit_integrity import seal_audit
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.admin_subscription_reconcile import AdminSubscriptionReconciliation, SubscriptionConflict, SubscriptionUnavailable
from guardoath.billing import BillingMode
from guardoath.dynamodb_store import DynamoDBTenantStore, to_item, from_item
from guardoath.subscription_reconciliation import StripeBinding
from guardoath.tenant_record import TenantRecord, EDITABLE_FIELDS
from guardoath.tenant_store import MemoryTenantStore

KEY = b"synthetic-reconciliation-audit-key-0123456789"
ACTOR = "12345678-1234-1234-1234-123456789012"
TENANT = "usr-reconcile"


@pytest.fixture(autouse=True)
def future_writer_phase(monkeypatch):
    """Retain every writer assertion while the production source is baseline.

    This override exists only inside tests. The deployed application has no
    phase override; test_subscription_compatibility_baseline exercises its
    actual no-introduction behavior and rollback support separately.
    """
    from guardoath import state_contracts
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_SUBSCRIPTION_STATE", True)


def tenant(**changes):
    return TenantRecord.model_validate({"tenant_id": TENANT, "display_name": "Synthetic account", "revision": 7,
        "demo": False, "status": "active", "billing_mode": "stripe", "payer_id": "cus_reconcile",
        "subscription_status": "inactive", "monthly_allowance": 0, "overage": "block",
        "key_hashes": ["a" * 64], "usage": {"2026-11": 10}, "input_check_pii": "block",
        "input_rule_actions": {"pii.email_address": "off"}, "output_rule_actions": {"pii.email_address": "block"},
        "input_attachment_csv_mode": "off", **changes})


def provider(state="active", **changes):
    return {"id": "sub_reconcile", "object": "subscription", "customer": "cus_reconcile",
        "status": state, "livemode": False, "pause_collection": None, "collection_method": "charge_automatically",
        "items": {"has_more": False, "data": [{"quantity": 1, "price": {"id": "price_starter"}}]}, **changes}


def request(**changes):
    return {"tenant_id": TENANT, "expected_revision": 7, "confirmation": "RECONCILE " + TENANT,
        "reason": "Restore verified subscription state", "actor": ACTOR,
        "idempotency_key": "same-request-123456789", **changes}


def service(db, snapshot=None, reads=None, resolver=None, plan=10000):
    def fetch(current):
        if reads is not None:
            reads.append(current.tenant_id)
        return StripeBinding(current.tenant_id, current.payer_id, current.stripe_subscription_id or "sub_reconcile"), snapshot or provider()
    return AdminSubscriptionReconciliation(db, "tenant_table", "audit_table", signing_key=KEY,
        resolve_snapshot=resolver or fetch, expected_price_id="price_starter", expected_livemode=False,
        plan_monthly_allowance=plan, clock=lambda: datetime(2026, 11, 23, tzinfo=timezone.utc))


@pytest.fixture
def db(tmp_path):
    database = AdminTestDynamo(tmp_path / "admin.sqlite3")
    database.seed("tenant_table", to_item(tenant()))
    yield database
    database.close()


def test_optional_billing_schema_does_not_expand_unrelated_records_but_preserves_real_caps():
    original = tenant()
    item = to_item(original)
    raw = json.loads(item["record"]["S"])
    assert "admin_monthly_allowance_cap" not in raw and "stripe_plan_monthly_allowance" not in raw
    assert from_item(item) == original
    capped = tenant(admin_monthly_allowance_cap=0,stripe_plan_monthly_allowance=10000)
    saved = to_item(capped)
    assert json.loads(saved["record"]["S"])["admin_monthly_allowance_cap"] == 0
    assert from_item(saved) == capped


def test_first_activation_persists_verified_binding_and_signed_audit_without_policy_or_key_changes(db):
    store = DynamoDBTenantStore("tenant_table", db)
    before = store.get(TENANT)
    db.seed("tenant_table", to_item(tenant(tenant_id="usr-unrelated", payer_id="cus_other")))
    other = store.get("usr-unrelated")
    result = service(db).apply(**request())
    after = store.get(TENANT)
    assert result["replayed"] is False and after.subscription_status == "active"
    assert after.monthly_allowance == 10000 and after.stripe_subscription_id == "sub_reconcile"
    assert after.stripe_plan_monthly_allowance == 10000 and after.admin_monthly_allowance_cap is None
    assert after.revision == 8 and db.transactions == 1
    for name in (*EDITABLE_FIELDS, "usage", "key_hashes", "status", "payer_id"):
        assert getattr(after, name) == getattr(before, name)
    assert store.get("usr-unrelated") == other
    audit = AdminAuditReader(db, "audit_table", verify_key=KEY).read(result["audit_event_id"])
    assert audit["integrity_status"] == "signed_verified" and audit["operation"] == "billing.reconcile"
    assert audit["change"]["before"] == "inactive" and audit["change"]["after"] == "active"
    assert "cus_reconcile" not in str(db.audit_items()) and "sub_reconcile" not in str(db.audit_items())


@pytest.mark.parametrize("state,expected", [("past_due", "past_due"), ("canceled", "canceled"),
    ("unpaid", "inactive"), ("incomplete", "inactive"), ("incomplete_expired", "inactive"),
    ("trialing", "inactive"), ("paused", "inactive")])
def test_every_nonactive_state_revokes_quota_and_retains_operator_controls(db, state, expected):
    db.seed("tenant_table", to_item(tenant(subscription_status="active", stripe_subscription_id="sub_reconcile",
        monthly_allowance=4000, admin_monthly_allowance_cap=4000, stripe_plan_monthly_allowance=10000,
        status="disabled")))
    outcome = service(db, provider(state)).apply(**request())
    saved = DynamoDBTenantStore("tenant_table", db).get(TENANT)
    assert outcome["subscription_status"] == expected and saved.monthly_allowance == 0
    assert saved.admin_monthly_allowance_cap == 4000 and saved.status == "disabled" and saved.overage == "block"


def test_real_owner_cap_survives_customer_reconciliation_and_reactivation(db):
    store = DynamoDBTenantStore("tenant_table", db)
    service(db).apply(**request())
    capped = AdminTenantActions(db, "tenant_table", "audit_table", audit_key=KEY).cap_monthly_allowance(
        tenant_id=TENANT, expected_revision=8, new_limit=4000, confirmation=TENANT,
        reason="Enforce approved customer budget", actor=ACTOR, idempotency_key="cap-request-123456789",
        validate_record=TenantRecord.model_validate)
    assert capped["monthly_allowance"] == 4000
    for state in ("active", "past_due", "active", "canceled", "active"):
        current = store.get(TENANT)
        saved = store.apply_verified_subscription(TENANT, customer_id="cus_reconcile", subscription_id="sub_reconcile",
            provider_subscription=provider(state), expected_revision=current.revision, plan_monthly_allowance=10000)
        assert saved.admin_monthly_allowance_cap == 4000
        assert saved.monthly_allowance == (4000 if state == "active" else 0)
        assert saved.usage == {"2026-11": 10} and saved.input_rule_actions["pii.email_address"] == "off"


@pytest.mark.parametrize("factory", [MemoryTenantStore, "dynamo"])
@pytest.mark.parametrize("saved,plans,expected,cap", [
    ({}, [10000, 5000, 10000], [10000, 5000, 10000], None),
    ({"stripe_subscription_id":"sub_reconcile","subscription_status":"active","monthly_allowance":7000},
        [5000,10000], [5000,7000], 7000),
    ({"stripe_subscription_id":"sub_reconcile","admin_monthly_allowance_cap":4000}, [10000], [4000], 4000),
    ({"stripe_subscription_id":"sub_reconcile"}, [10000], [0], 0),
    ({"admin_monthly_allowance_cap":0}, [10000], [0], 0),
    ({"stripe_subscription_id":"sub_reconcile","subscription_status":"active","monthly_allowance":4000,
      "stripe_plan_monthly_allowance":10000}, [10000], [4000], 4000),
])
def test_plan_transitions_preserve_caps_in_both_authoritative_stores(db, factory, saved, plans, expected, cap):
    initial = tenant(**saved)
    if factory == "dynamo":
        db.seed("tenant_table", to_item(initial))
        store = DynamoDBTenantStore("tenant_table", db)
    else:
        store = factory((initial,))
    for plan, limit in zip(plans, expected):
        current = store.get(TENANT)
        result = store.apply_verified_subscription(TENANT, customer_id="cus_reconcile", subscription_id="sub_reconcile",
            provider_subscription=provider(), expected_revision=current.revision, plan_monthly_allowance=plan)
        assert result.monthly_allowance == limit and result.admin_monthly_allowance_cap == cap
    for field in ("admin_monthly_allowance_cap", "stripe_plan_monthly_allowance"):
        with pytest.raises(ValueError):
            store.update_checks(TENANT, {field: None}, result.revision)


@pytest.mark.parametrize("change", [
    {"customer":"cus_other"}, {"id":"sub_other"}, {"livemode":True}, {"status":"mystery"},
    {"items":{"has_more":True,"data":[]}},
    {"items":{"has_more":False,"data":[{"quantity":0,"price":{"id":"price_starter"}}]}},
    {"items":{"has_more":False,"data":[{"quantity":True,"price":{"id":"price_starter"}}]}},
    {"items":{"has_more":False,"data":[{"quantity":1,"price":{"id":"price_other"}}]}},
])
def test_bad_provider_cannot_mutate_tenant_or_audit(db, change):
    before = db.get_item(TableName="tenant_table", Key={"tenant_id":{"S":TENANT}})
    with pytest.raises(SubscriptionUnavailable):
        service(db, provider(**change)).apply(**request())
    assert db.get_item(TableName="tenant_table", Key={"tenant_id":{"S":TENANT}}) == before
    assert db.transactions == 0 and not db.audit_items()


@pytest.mark.parametrize("pause", ["missing", {"behavior":"void","resumes_at":None}])
def test_active_with_unknown_or_paused_payment_collection_does_not_grant(db, pause):
    snapshot = provider()
    if pause == "missing":
        snapshot.pop("pause_collection")
    else:
        snapshot["pause_collection"] = pause
    with pytest.raises(SubscriptionConflict):
        service(db, snapshot).apply(**request())
    assert db.transactions == 0


@pytest.mark.parametrize("method", ["missing", "send_invoice"])
def test_active_manual_or_unknown_collection_method_does_not_grant(db, method):
    snapshot = provider(collection_method=method)
    if method == "missing": snapshot.pop("collection_method")
    with pytest.raises(SubscriptionConflict): service(db, snapshot).apply(**request())
    assert db.transactions == 0


def test_quantity_other_than_server_single_item_plan_cannot_grant(db):
    snapshot = provider()
    snapshot["items"]["data"][0]["quantity"] = 2
    with pytest.raises(SubscriptionConflict): service(db, snapshot).apply(**request())
    assert db.transactions == 0


def test_nonactive_quantity_change_still_revokes_existing_active_entitlement(db):
    db.seed("tenant_table",to_item(tenant(subscription_status="active",stripe_subscription_id="sub_reconcile",
        monthly_allowance=10000,stripe_plan_monthly_allowance=10000)))
    snapshot = provider("past_due",collection_method="send_invoice",pause_collection={"behavior":"void"})
    snapshot["items"]["data"][0]["quantity"] = 2
    result = service(db,snapshot).apply(**request())
    assert result["subscription_status"] == "past_due" and result["monthly_allowance"] == 0
    assert DynamoDBTenantStore("tenant_table",db).get(TENANT).overage == "block"


@pytest.mark.parametrize("field", ["status", "monthly_allowance", "overage"])
def test_missing_saved_account_controls_are_not_filled_from_defaults(db, field):
    item = to_item(tenant())
    raw = json.loads(item["record"]["S"])
    raw.pop(field)
    item["record"]["S"] = json.dumps(raw)
    db.seed("tenant_table", item)
    with pytest.raises(SubscriptionConflict):
        service(db, resolver=lambda _: pytest.fail("Unknown account state must not call Stripe")).apply(**request())
    assert db.transactions == 0


def test_durable_replay_after_lost_response_never_repeats_provider_or_write(db):
    calls = []
    action = service(db, reads=calls)
    db.lose_transaction_response = True
    with pytest.raises(SubscriptionUnavailable):
        action.apply(**request())
    assert db.transactions == 1
    outcome = action.apply(**request())
    assert outcome["replayed"] is True and outcome["revision"] == 8
    assert calls == [TENANT] and db.transactions == 1
    with pytest.raises(SubscriptionConflict):
        action.apply(**request(reason="Changed reason on consumed reference"))
    with pytest.raises(SubscriptionConflict):
        action.apply(**request(expected_revision=8))
    assert calls == [TENANT] and db.transactions == 1
    path = db.connection.execute("PRAGMA database_list").fetchone()[2]
    reopened = AdminTestDynamo(path)
    try:
        assert service(reopened, resolver=lambda _: pytest.fail("Replay must not call Stripe")).apply(**request())["replayed"]
    finally:
        reopened.close()


@pytest.mark.parametrize("field,value", [
    ("plan_limit", {"N":"9999"}), ("cap_limit", {"N":"4000"}),
    ("request_fingerprint", {"S":"0" * 64}), ("new_limit", {"N":"4000"}),
    ("new_subscription_status", {"S":"inactive"}), ("integrity_mac", {"S":"0" * 64}),
    ("integrity_mac", None),
])
def test_tampered_idempotent_receipts_never_replay_or_write(db, field, value):
    calls = []
    action = service(db, reads=calls)
    outcome = action.apply(**request())
    item = db.get_item(TableName="audit_table", Key={"event_id":{"S":outcome["audit_event_id"]}})["Item"]
    if value is None: item.pop(field)
    else: item[field] = value
    db.seed("audit_table", item)
    with pytest.raises(SubscriptionUnavailable): action.apply(**request())
    assert calls == [TENANT] and db.transactions == 1


@pytest.mark.parametrize("changes", [
    {"cap_limit":{"N":"4000"}}, {"provider_status":{"S":"canceled"}},
    {"plan_limit":{"N":"0"}}, {"new_limit":{"N":"0"}},
])
def test_even_signed_impossible_receipt_is_not_replayed(db, changes):
    calls = []
    action = service(db, reads=calls)
    outcome = action.apply(**request())
    item = db.get_item(TableName="audit_table", Key={"event_id":{"S":outcome["audit_event_id"]}})["Item"]
    item.pop("integrity_mac")
    db.seed("audit_table", seal_audit({**item, **changes}, KEY))
    with pytest.raises(SubscriptionUnavailable): action.apply(**request())
    assert calls == [TENANT] and db.transactions == 1


def test_replay_is_historical_even_after_later_tenant_changes(db):
    calls = []
    action = service(db, reads=calls)
    original = action.apply(**request())
    store = DynamoDBTenantStore("tenant_table", db)
    later = store.update_checks(TENANT, {"input_check_pii":"flag"}, 8)
    replay = action.apply(**request())
    assert {**original, "replayed":True} == replay
    assert store.get(TENANT) == later and later.revision == 9
    assert calls == [TENANT] and db.transactions == 1


@pytest.mark.parametrize("race", ["revision", "same_revision"])
def test_racing_tenant_writer_is_preserved_atomically(db, race):
    def resolver(current):
        item = db.get_item(TableName="tenant_table", Key={"tenant_id":{"S":TENANT}})["Item"]
        source = json.loads(item["record"]["S"])
        source["display_name"] = "Concurrent owner change"
        item["record"]["S"] = json.dumps(source)
        if race == "revision": item["revision"]["N"] = "8"
        db.seed("tenant_table", item)
        return StripeBinding(TENANT, "cus_reconcile", "sub_reconcile"), provider()
    with pytest.raises(SubscriptionUnavailable):
        service(db, resolver=resolver).apply(**request())
    saved = DynamoDBTenantStore("tenant_table", db).get(TENANT)
    assert saved.display_name == "Concurrent owner change" and saved.subscription_status == "inactive"
    assert not db.audit_items()


def test_audit_failure_rolls_back_entitlement(db):
    db.fail_audit = True
    with pytest.raises(SubscriptionUnavailable): service(db).apply(**request())
    assert DynamoDBTenantStore("tenant_table", db).get(TENANT).subscription_status == "inactive"
    assert db.transactions == 0


@pytest.mark.parametrize("corrupt", ['{"tenant_id":"usr-reconcile","tenant_id":"usr-other"}', '{"x":NaN}'])
def test_ambiguous_saved_json_fails_before_provider(db, corrupt):
    item = to_item(tenant()); item["record"]["S"] = corrupt
    db.seed("tenant_table", item)
    with pytest.raises(SubscriptionUnavailable):
        service(db, resolver=lambda _: pytest.fail("Corrupt tenant must not call Stripe")).apply(**request())
    assert db.transactions == 0


def test_real_api_enforces_owner_mfa_origin_audit_and_forbids_browser_entitlement_claims(db):
    reads = []
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=lambda token:
        VerifiedAdmin(ACTOR, frozenset({"owner"}), token == "owner") if token in ("owner","no-mfa") else None,
        billing_reconciler=service(db, reads=reads), require_persistent_read_audit=True,
        read_auditor=AdminSensitiveReadAuditor(db,"audit_table",signing_key=KEY),
        audit_reader=AdminAuditReader(db,"audit_table",verify_key=KEY)))
    client = TestClient(app)
    payload = {k:v for k,v in request().items() if k not in ("actor","tenant_id")}
    url = "/v1/admin/subscriptions/" + TENANT + "/reconcile"
    headers = {"X-GuardOath-Admin-Session":"owner","Origin":"https://guardoath.com"}
    for bad in ({}, {"X-GuardOath-Key":"owner"}, {**headers,"X-GuardOath-Admin-Session":"customer"},
                {**headers,"X-GuardOath-Admin-Session":"no-mfa"}, {**headers,"Origin":"https://evil.invalid"}):
        assert client.post(url, json=payload, headers=bad).status_code in (401,403)
    for key in ("monthly_allowance","subscription_status","payer_id","stripe_subscription_id","price_id","actor"):
        assert client.post(url,json={**payload,key:"forged"},headers=headers).status_code == 422
    assert not reads and db.transactions == 0
    db.fail_audit = True
    assert client.post(url,json=payload,headers=headers).status_code == 503
    assert not reads
    db.fail_audit = False
    response = client.post(url,json=payload,headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["read_audit_event_id"] and result["subscription_status"] == "active"
    assert client.get("/v1/admin/audit/"+result["audit_event_id"],headers=headers).json()["audit"]["integrity_status"] == "signed_verified"
    assert client.post(url,json=payload,headers=headers).json()["replayed"] is True
    assert reads == [TENANT] and db.transactions == 1
