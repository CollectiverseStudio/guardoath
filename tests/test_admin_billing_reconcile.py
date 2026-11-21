"""Owner Stripe delinquency downgrade: provider authority, transactional audit, replay and API."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guardoath.admin_billing import AdminSubscriptionReporter
from guardoath.admin_billing_reconcile import (
    AdminBillingDowngrade, BillingDowngradeConflict, BillingDowngradeUnavailable)
from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor
from guardoath.admin_audit_integrity import verify_audit
from guardoath.admin_console import VerifiedAdmin, create_admin_router

KEY = b"x" * 32
ACTOR = "12345678-1234-1234-1234-123456789012"
TENANT = "usr-test"
PRICE = "price_starter"

def record():
    return {"tenant_id":TENANT,"display_name":"Customer","demo":False,"status":"active",
            "billing_mode":"stripe","payer_id":"cus_safe123","stripe_subscription_id":"sub_safe123",
            "subscription_status":"active","monthly_allowance":1000,"overage":"allow_and_record",
            "key_hashes":[],"input_check_pii":"block","input_attachment_csv_mode":"off"}

def provider(status="past_due"):
    return {"object":"subscription","id":"sub_safe123","customer":"cus_safe123",
            "status":status,"livemode":False,"items":{"has_more":False,"data":[
                {"quantity":1,"price":{"id":PRICE}}]}}

class FakeDDB:
    def __init__(self):
        self.doc=record();self.rev=7;self.events={};self.transactions=[];self.fail=False
    def get_item(self,**k):
        if k["TableName"]=="tenant_table":
            return {"Item":{"revision":{"N":str(self.rev)},"record":{"S":json.dumps(self.doc)}}}
        key=k["Key"]["event_id"]["S"]
        return {"Item":self.events[key]} if key in self.events else {}
    def put_item(self,**k):
        if self.fail:raise RuntimeError("Audit unavailable")
        assert k["ConditionExpression"]=="attribute_not_exists(event_id)"
        item=k["Item"];key=item["event_id"]["S"]
        assert key not in self.events
        self.events[key]=item
    def transact_write_items(self,**k):
        if self.fail:raise RuntimeError("Audit unavailable")
        update,audit=k["TransactItems"]
        assert update["Update"]["ConditionExpression"]=="attribute_exists(tenant_id) AND #revision = :prior AND #record = :original"
        values=update["Update"]["ExpressionAttributeValues"]
        assert int(values[":prior"]["N"])==self.rev
        # Model a same-revision legacy writer racing the provider read.
        if values[":original"]["S"] != json.dumps(self.doc):
            raise RuntimeError("ConditionalCheckFailed")
        event=audit["Put"]["Item"]
        assert event["event_id"]["S"] not in self.events
        self.events[event["event_id"]["S"]]=event
        self.doc=json.loads(values[":record"]["S"]);self.rev=int(values[":next"]["N"])
        self.transactions.append(k)

def make(db, state="past_due", reads=None):
    def fetch(*args):
        if reads is not None:reads.append(args)
        return provider(state)
    reporter=AdminSubscriptionReporter(fetch,expected_price_id=PRICE,expected_livemode=False)
    return AdminBillingDowngrade(db,"tenant_table","audit_table",signing_key=KEY,
        reporter=reporter,validate_record=lambda d:SimpleNamespace(**d,model_dump=lambda:d),
        clock=lambda:datetime(2026,11,20,tzinfo=timezone.utc))

def body():
    return {"tenant_id":TENANT,"expected_revision":7,
            "confirmation":"RECONCILE "+TENANT,"reason":"Verified billing exception",
            "actor":ACTOR,"idempotency_key":"idempotency123456789"}

@pytest.mark.parametrize("state,expected",[
    ("past_due","past_due"),("canceled","canceled"),("unpaid","inactive"),
    ("trialing","inactive"),("incomplete","inactive"),("incomplete_expired","inactive"),
    ("paused","inactive")])
def test_provider_verified_nonactive_downgrade_preserves_policy_and_writes_signed_audit(state,expected):
    db=FakeDDB();before=record()
    result=make(db,state).apply(**body())
    assert db.rev==8 and result["subscription_status"]==expected
    assert db.doc["monthly_allowance"]==0 and db.doc["overage"]=="block"
    for field in ("payer_id","stripe_subscription_id","key_hashes","status","input_check_pii",
                  "input_attachment_csv_mode"):
        assert db.doc[field]==before[field]
    assert len(db.transactions)==1
    receipt=db.events[result["audit_event_id"]]
    assert verify_audit(receipt,KEY)
    assert "cus_safe123" not in str(receipt) and "sub_safe123" not in str(receipt)
    parsed=AdminAuditReader(db,"audit_table",verify_key=KEY).read(result["audit_event_id"])
    assert parsed["integrity_status"]=="signed_verified"
    assert parsed["change"]["provider_status"]==state
    assert parsed["change"]["after"]==expected and parsed["change"]["allowance_after"]==0

def test_active_provider_does_not_downgrade_or_grant():
    db=FakeDDB()
    with pytest.raises(BillingDowngradeConflict):make(db,"active").apply(**body())
    assert db.rev==7 and not db.transactions and db.doc==record()

def test_stale_revision_and_repeat_key_cannot_mutate_again():
    db=FakeDDB()
    make(db).apply(**body())
    with pytest.raises(BillingDowngradeConflict):make(db).apply(**body())
    assert len(db.transactions)==1

@pytest.mark.parametrize("bad",[
    {"confirmation":"wrong"},{"reason":"short"},{"actor":"customer"},
    {"expected_revision":True},{"idempotency_key":"tiny"}])
def test_unconfirmed_invalid_request_never_calls_provider_or_writes(bad):
    db=FakeDDB();reads=[];params=body()|bad
    with pytest.raises(ValueError):make(db,reads=reads).apply(**params)
    assert not reads and not db.transactions

@pytest.mark.parametrize("bad",[
    {"demo":True},{"billing_mode":"internal"},{"subscription_status":"past_due"},
    {"payer_id":None},{"stripe_subscription_id":None}])
def test_unbound_or_not_active_avoids_provider_calls(bad):
    db=FakeDDB();db.doc.update(bad);reads=[]
    with pytest.raises(BillingDowngradeConflict):make(db,reads=reads).apply(**body())
    assert not reads and not db.transactions


def test_same_revision_out_of_band_edit_during_stripe_read_aborts_atomically():
    db=FakeDDB()
    def read_and_race(*_args):
        # A legacy writer changes a customer field without incrementing its
        # revision after our strongly consistent read but before transaction.
        db.doc["display_name"]="Concurrent verified name"
        return provider("past_due")
    reporter=AdminSubscriptionReporter(
        read_and_race,expected_price_id=PRICE,expected_livemode=False)
    action=AdminBillingDowngrade(
        db,"tenant_table","audit_table",signing_key=KEY,
        reporter=reporter,validate_record=lambda d:SimpleNamespace(**d,model_dump=lambda:d),
        clock=lambda:datetime(2026,11,20,tzinfo=timezone.utc))
    with pytest.raises(BillingDowngradeUnavailable):
        action.apply(**body())
    assert db.rev==7 and db.doc["display_name"]=="Concurrent verified name"
    assert db.doc["subscription_status"]=="active"
    assert db.doc["monthly_allowance"]==1000
    assert db.doc["input_check_pii"]=="block"
    assert not db.events and not db.transactions

def test_audit_outage_never_writes():
    db=FakeDDB();db.fail=True
    with pytest.raises(BillingDowngradeUnavailable):make(db).apply(**body())
    assert db.rev==7 and db.doc==record()

def test_authenticated_api_denies_demo_key_cross_origin_and_unlogged_provider_read():
    db=FakeDDB();reads=[]
    reader=AdminSensitiveReadAuditor(db,"audit_table",signing_key=KEY)
    app=FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token:VerifiedAdmin(ACTOR,frozenset({"owner"}),True)
                             if token=="owner" else None,
        billing_downgrade=make(db,reads=reads),read_auditor=reader,
        audit_reader=AdminAuditReader(db,"audit_table",verify_key=KEY),
        require_persistent_read_audit=True))
    c=TestClient(app);url="/v1/admin/subscriptions/usr-test/downgrade"
    b={k:v for k,v in body().items() if k not in ("tenant_id","actor")}
    assert c.post(url,json=b).status_code==401
    assert c.post(url,json=b,headers={"X-GuardOath-Key":"owner"}).status_code==401
    assert c.post(url,json=b,headers={"X-GuardOath-Admin-Session":"owner",
                                   "Origin":"https://evil.example"}).status_code==403
    assert not reads and not db.transactions
    db.fail=True
    h={"X-GuardOath-Admin-Session":"owner","Origin":"https://guardoath.com"}
    assert c.post(url,json=b,headers=h).status_code==503
    assert not reads and not db.transactions
    db.fail=False
    resp=c.post(url,json=b,headers=h)
    assert resp.status_code==200,resp.text
    assert reads==[(TENANT,"cus_safe123","sub_safe123")]
    assert resp.json()["read_audit_event_id"] in db.events
    check=c.get("/v1/admin/audit/"+resp.json()["audit_event_id"],headers=h)
    assert check.status_code==200 and check.json()["audit"]["integrity_status"]=="signed_verified"
    db.events[resp.json()["audit_event_id"]]["provider_status"]["S"]="active"
    assert c.get("/v1/admin/audit/"+resp.json()["audit_event_id"],headers=h).status_code==503
