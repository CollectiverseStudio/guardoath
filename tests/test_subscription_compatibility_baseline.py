"""Actual baseline writers stay legacy-compatible and enforce future state.

The retained writer implementation has its complete separate regression suite.
Here only seeding a future release uses a scoped source-phase test override;
all operations under test run with the actual baseline production phase.
"""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from admin_test_support import AdminTestDynamo
from guardoath import state_contracts
from guardoath.admin_actions import AdminTenantActions, AdminUnboundZeroCap
from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor
from guardoath.admin_billing import AdminSubscriptionReporter
from guardoath.admin_billing_reconcile import AdminBillingDowngrade
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.admin_subscription_reconcile import SubscriptionUnavailable
from guardoath.dynamodb_store import DynamoDBTenantStore, record_json, to_item
from guardoath.tenant_record import TenantRecord
from guardoath.tenant_store import MemoryTenantStore, UsageLimitReached
from test_admin_subscription_reconcile import ACTOR, KEY, TENANT, provider, request, service, tenant
from test_dynamodb_store import FakeDynamoDB

FIELDS = ("admin_monthly_allowance_cap", "stripe_plan_monthly_allowance")


@pytest.fixture
def db(tmp_path):
    database = AdminTestDynamo(tmp_path / "baseline.sqlite3")
    yield database
    database.close()


def make_store(kind, db):
    return MemoryTenantStore() if kind == "memory" else DynamoDBTenantStore("tenant_table", db)


def saved_document(store, db):
    if isinstance(store, DynamoDBTenantStore):
        return json.loads(db.get_item(TableName="tenant_table", Key={"tenant_id":{"S":TENANT}},
            ConsistentRead=True)["Item"]["record"]["S"])
    return json.loads(record_json(store.get(TENANT)))


def reconcile(store, state="active", plan=10000):
    current = store.get(TENANT)
    return store.apply_verified_subscription(TENANT, customer_id="cus_reconcile", subscription_id="sub_reconcile",
        provider_subscription=provider(state), expected_revision=current.revision, plan_monthly_allowance=plan)


def action_args(current, key):
    return {"tenant_id":TENANT,"expected_revision":current.revision,"confirmation":TENANT,
        "reason":"Verify compatibility release safeguards","actor":ACTOR,"idempotency_key":key}


def test_source_phase_and_manifest_cannot_be_enabled_by_environment(monkeypatch):
    assert state_contracts.ENABLE_NEW_SUBSCRIPTION_STATE is False
    for name in ("ENABLE_NEW_SUBSCRIPTION_STATE", "GUARDOATH_ENABLE_NEW_SUBSCRIPTION_STATE",
                 "GUARDOATH_SUBSCRIPTION_WRITER_ENABLED", "GUARDOATH_ADMIN_TEST_WRITER_PHASE"):
        monkeypatch.setenv(name, "1")
    assert state_contracts.new_subscription_state_enabled() is False
    manifest = state_contracts.release_metadata("a" * 40)
    assert manifest["supported_state_contracts"] == ["subscription-cap-provenance-v1", "stripe-period-end-cancellation-v1",
                                                   "catalog-publication-v1"]
    assert manifest["introduced_state_contracts"] == []


@pytest.mark.parametrize("kind", ["memory", "dynamo"])
@pytest.mark.parametrize("provenance", [{"admin_monthly_allowance_cap":0},
    {"admin_monthly_allowance_cap":4000}, {"stripe_plan_monthly_allowance":10000}])
def test_baseline_rejects_new_provenance_records_before_persistence(db,kind,provenance):
    store = make_store(kind, db)
    with pytest.raises(ValueError,match="compatibility release"):
        store.create(tenant(**provenance))
    assert store.get(TENANT) is None and not db.audit_items()


@pytest.mark.parametrize("kind", ["memory", "dynamo"])
def test_legacy_customer_activation_caps_delinquency_and_recovery_never_add_provenance(db,kind):
    store = make_store(kind, db)
    before = store.create(tenant())
    for state,plan,expected in [("active",10000,10000),("active",5000,5000),
            ("active",10000,5000),("past_due",10000,0),("active",10000,0),
            ("canceled",10000,0)]:
        current = reconcile(store,state,plan)
        assert current.monthly_allowance == expected and current.overage == "block"
        assert all(getattr(current,name) is None for name in FIELDS)
        assert not any(name in saved_document(store,db) for name in FIELDS)
        for field in ("usage","key_hashes","input_rule_actions","output_rule_actions","status"):
            assert getattr(current,field) == getattr(before,field)
    assert not db.audit_items()


def test_legacy_owner_cap_and_every_existing_tenant_action_keep_legacy_record_shape(db):
    db.seed("tenant_table",to_item(tenant(subscription_status="active",stripe_subscription_id="sub_reconcile",
        monthly_allowance=10000)))
    store = DynamoDBTenantStore("tenant_table",db)
    actions = AdminTenantActions(db,"tenant_table","audit_table",audit_key=KEY)
    actions.cap_monthly_allowance(**action_args(store.get(TENANT),"baseline-cap-123456789"),
        new_limit=4000,validate_record=TenantRecord.model_validate)
    assert reconcile(store).monthly_allowance == 4000
    for status in ("disabled","active"):
        actions.change_status(**action_args(store.get(TENANT),"baseline-status-"+status),status=status)
        assert not any(name in saved_document(store,db) for name in FIELDS)
    actions.change_rule_action(**action_args(store.get(TENANT),"baseline-rule-123456789"),
        stage="output",rule_id="pii.email_address",action="off",validate_record=TenantRecord.model_validate,
        rule_exists=lambda rule:rule=="pii.email_address")
    actions.revoke_all_keys(**action_args(store.get(TENANT),"baseline-keys-123456789"),
        validate_record=TenantRecord.model_validate)
    current = store.get(TENANT)
    store.update_checks(TENANT,{"input_check_pii":"flag"},current.revision)
    current = store.get(TENANT)
    store.set_key_hashes(TENANT,("b"*64,),current.revision)
    assert not any(name in saved_document(store,db) for name in FIELDS)
    assert store.get(TENANT).monthly_allowance == 4000
    reader = AdminAuditReader(db,"audit_table",verify_key=KEY)
    for event in db.audit_items():
        assert event["operation"]["S"] != "billing.reconcile"
        assert reader.read(event["event_id"]["S"])["integrity_status"] == "signed_verified"


@pytest.mark.parametrize("kind", ["memory", "dynamo"])
@pytest.mark.parametrize("writer_phase", [False,True])
@pytest.mark.parametrize("billing", ["internal", "stripe"])
def test_owner_positive_cap_survives_customer_binding_and_first_activation(db,monkeypatch,kind,writer_phase,billing):
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_SUBSCRIPTION_STATE",writer_phase)
    original = tenant(monthly_allowance=10000,overage="allow_and_record",billing_mode=billing,
        payer_id="cus_reconcile" if billing=="stripe" else None)
    db.seed("tenant_table",to_item(original))
    source = DynamoDBTenantStore("tenant_table",db)
    AdminTenantActions(db,"tenant_table","audit_table",audit_key=KEY).cap_monthly_allowance(
        **action_args(source.get(TENANT),"before-first-activation-cap"),new_limit=4000,
        validate_record=TenantRecord.model_validate)
    store = source if kind=="dynamo" else MemoryTenantStore((source.get(TENANT),))
    if billing=="internal":
        current = store.get(TENANT)
        store.bind_stripe_customer(TENANT,"cus_reconcile",current.revision)
    current = reconcile(store)
    assert current.monthly_allowance == 4000 and current.overage == "block"
    assert current.stripe_subscription_id == "sub_reconcile" and current.subscription_status == "active"
    assert current.admin_monthly_allowance_cap == (4000 if writer_phase else None)
    assert current.input_rule_actions == original.input_rule_actions and current.usage == original.usage


@pytest.mark.parametrize("allowance,overage", [(10000,"block"),(0,"allow_and_record")])
def test_baseline_refuses_ambiguous_unbound_zero_cap_without_audit_or_tenant_write(db,allowance,overage):
    original = tenant(monthly_allowance=allowance,overage=overage)
    db.seed("tenant_table",to_item(original))
    store = DynamoDBTenantStore("tenant_table",db)
    with pytest.raises(AdminUnboundZeroCap):
        AdminTenantActions(db,"tenant_table","audit_table",audit_key=KEY).cap_monthly_allowance(
            **action_args(store.get(TENANT),"baseline-zero-unbound-cap"),new_limit=0,
            validate_record=TenantRecord.model_validate)
    assert store.get(TENANT) == original and db.transactions == 0 and not db.audit_items()


@pytest.mark.parametrize("changes", [{"stripe_subscription_id":"sub_reconcile"},
    {"stripe_plan_monthly_allowance":10000}, {"admin_monthly_allowance_cap":10000}])
def test_baseline_zero_caps_remain_supported_when_binding_or_provenance_disambiguates_them(db,changes):
    original = tenant(monthly_allowance=10000,**changes)
    db.seed("tenant_table",to_item(original))
    store = DynamoDBTenantStore("tenant_table",db)
    AdminTenantActions(db,"tenant_table","audit_table",audit_key=KEY).cap_monthly_allowance(
        **action_args(store.get(TENANT),"baseline-safe-zero-cap"),new_limit=0,validate_record=TenantRecord.model_validate)
    assert reconcile(store).monthly_allowance == 0


def test_unbound_zero_cap_api_gives_actionable_release_limitation(db):
    original = tenant(monthly_allowance=10000)
    db.seed("tenant_table",to_item(original))
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token:VerifiedAdmin(ACTOR,frozenset({"owner"}),True) if token=="owner" else None,
        admin_mutator=AdminTenantActions(db,"tenant_table","audit_table",audit_key=KEY),
        record_validator=TenantRecord.model_validate))
    payload = {k:v for k,v in action_args(original,"baseline-zero-api-refusal").items() if k not in ("tenant_id","actor")}
    with TestClient(app) as client:
        response = client.post("/v1/admin/tenants/"+TENANT+"/allowance-cap",
            headers={"X-GuardOath-Admin-Session":"owner","Origin":"https://guardoath.com"},json={**payload,"new_limit":0})
    assert response.status_code == 409
    assert response.headers["X-GuardOath-Admin-Error"] == "unbound-zero-cap"
    assert "Use Suspend to block access" in response.json()["detail"]
    assert "refresh" not in response.json()["detail"].lower()
    assert db.transactions == 0 and not db.audit_items()


@pytest.mark.parametrize("kind", ["memory", "dynamo"])
@pytest.mark.parametrize("cap", [0,4000])
def test_rollback_to_baseline_preserves_existing_future_caps_across_recovery_and_other_writes(db,monkeypatch,kind,cap):
    store = make_store(kind,db)
    with monkeypatch.context() as writer:
        writer.setattr(state_contracts,"ENABLE_NEW_SUBSCRIPTION_STATE",True)
        before = store.create(tenant(subscription_status="active",stripe_subscription_id="sub_reconcile",
            monthly_allowance=cap,admin_monthly_allowance_cap=cap,stripe_plan_monthly_allowance=10000))
    assert state_contracts.new_subscription_state_enabled() is False
    for state,plan in (("active",10000),("past_due",10000),("active",5000),("active",10000)):
        current = reconcile(store,state,plan)
        assert current.admin_monthly_allowance_cap == cap and current.stripe_plan_monthly_allowance == plan
        assert current.monthly_allowance == (min(plan,cap) if state=="active" else 0)
        assert current.usage == before.usage and current.input_rule_actions == before.input_rule_actions
        assert saved_document(store,db)["admin_monthly_allowance_cap"] == cap
    current = store.get(TENANT)
    store.update_checks(TENANT,{"input_check_pii":"flag"},current.revision)
    current = store.get(TENANT)
    store.set_key_hashes(TENANT,("b"*64,),current.revision)
    current = store.get(TENANT)
    assert current.admin_monthly_allowance_cap == cap and current.stripe_plan_monthly_allowance == 10000
    assert current.monthly_allowance == cap and current.overage == "block"


@pytest.mark.parametrize("kind", ["memory", "dynamo"])
@pytest.mark.parametrize("cap", [0,2])
def test_existing_cap_actually_blocks_usage_after_rollback(monkeypatch,kind,cap):
    record = tenant(monthly_allowance=cap,admin_monthly_allowance_cap=cap,
        stripe_plan_monthly_allowance=10000,subscription_status="active",usage={})
    if kind == "memory":
        with monkeypatch.context() as writer:
            writer.setattr(state_contracts,"ENABLE_NEW_SUBSCRIPTION_STATE",True)
            store = MemoryTenantStore((record,))
    else:
        boundary = FakeDynamoDB()
        boundary.items[TENANT] = to_item(record)
        store = DynamoDBTenantStore("guardoath-tenants",boundary)
    assert state_contracts.new_subscription_state_enabled() is False
    for expected in range(1,cap+1):
        assert store.count_check(TENANT,"2026-11") == (expected,False)
    with pytest.raises(UsageLimitReached):
        store.count_check(TENANT,"2026-11")
    assert store.get(TENANT).used("2026-11") == cap


@pytest.mark.parametrize("existing", [{"stripe_plan_monthly_allowance":10000},
    {"stripe_plan_monthly_allowance":10000,"admin_monthly_allowance_cap":10000}])
def test_baseline_owner_can_lower_cap_on_existing_future_contract(db,existing):
    db.seed("tenant_table",to_item(tenant(monthly_allowance=10000,subscription_status="active",
        stripe_subscription_id="sub_reconcile",**existing)))
    store = DynamoDBTenantStore("tenant_table",db)
    actions = AdminTenantActions(db,"tenant_table","audit_table",audit_key=KEY)
    actions.cap_monthly_allowance(**action_args(store.get(TENANT),"baseline-existing-cap-123"),
        new_limit=4000,validate_record=TenantRecord.model_validate)
    for state in ("past_due","active"):
        current = reconcile(store,state)
        assert current.admin_monthly_allowance_cap == 4000
        assert current.monthly_allowance == (4000 if state=="active" else 0)


def test_baseline_replays_existing_signed_receipt_but_cannot_create_a_new_one(db,monkeypatch):
    db.seed("tenant_table",to_item(tenant()))
    with monkeypatch.context() as writer:
        writer.setattr(state_contracts,"ENABLE_NEW_SUBSCRIPTION_STATE",True)
        original = service(db).apply(**request())
    before = DynamoDBTenantStore("tenant_table",db).get(TENANT)
    prior_transactions = db.transactions
    reads = []
    reconciler = service(db,reads=reads)
    assert reconciler.apply(**request()) == {**original,"replayed":True}
    assert reads == [] and db.transactions == prior_transactions
    with pytest.raises(SubscriptionUnavailable,match="compatibility release"):
        reconciler.apply(**request(expected_revision=before.revision,idempotency_key="new-baseline-request-123"))
    assert reads == [] and db.transactions == prior_transactions
    assert DynamoDBTenantStore("tenant_table",db).get(TENANT) == before
    event = next(row for row in db.audit_items() if row["event_id"]["S"] == original["audit_event_id"])
    event["plan_limit"] = {"N":"999999"}
    db.seed("audit_table",event)
    with pytest.raises(SubscriptionUnavailable): reconciler.apply(**request())
    assert db.transactions == prior_transactions


def test_baseline_router_keeps_verified_reads_and_downgrade_but_never_mounts_full_writer(db):
    db.seed("tenant_table",to_item(tenant(monthly_allowance=10000,subscription_status="active",
        stripe_subscription_id="sub_reconcile")))
    store = DynamoDBTenantStore("tenant_table",db)
    reporter = AdminSubscriptionReporter(lambda *_:provider(),expected_price_id="price_starter",expected_livemode=False)
    app = FastAPI()
    app.include_router(create_admin_router(tenant_store=store,
        verify_cognito_admin=lambda token:VerifiedAdmin(ACTOR,frozenset({"owner"}),True) if token=="owner" else None,
        billing_reporter=reporter,billing_reconciler=service(db),
        billing_downgrade=AdminBillingDowngrade(db,"tenant_table","audit_table",signing_key=KEY,
            reporter=reporter,validate_record=TenantRecord.model_validate),
        audit_reader=AdminAuditReader(db,"audit_table",verify_key=KEY),
        read_auditor=AdminSensitiveReadAuditor(db,"audit_table",signing_key=KEY),require_persistent_read_audit=True))
    headers = {"X-GuardOath-Admin-Session":"owner","Origin":"https://guardoath.com"}
    with TestClient(app) as client:
        features = client.get("/v1/admin/features",headers=headers).json()
        assert features["billing_reconcile"] is False
        assert features["billing_verify"] is True and features["billing_downgrade"] is True
        response = client.get("/v1/admin/subscriptions/"+TENANT,headers=headers)
        assert response.status_code == 200 and response.json()["subscription"]["verified"] is True
        payload = {k:v for k,v in request().items() if k not in ("actor","tenant_id")}
        assert client.post("/v1/admin/subscriptions/"+TENANT+"/reconcile",headers=headers,json=payload).status_code == 404
        assert client.get("/v1/admin/features").status_code == 401
    assert db.transactions == 0
    assert all(row["operation"]["S"] != "billing.reconcile" for row in db.audit_items())
