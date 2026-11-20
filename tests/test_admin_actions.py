from datetime import datetime, timezone
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from guardoath.admin_actions import AdminTenantActions, AdminActionConflict, AdminActionUnavailable
from guardoath.admin_console import VerifiedAdmin, create_admin_router

def all_routes(routes):
    for route in routes:
        if getattr(route, "path", None) is not None:
            yield route
        nested = getattr(route, "routes", None)
        if nested is None:
            nested = getattr(getattr(route, "router", None), "routes", ())
        if nested:
            yield from all_routes(nested)


SUB = "12345678-1234-1234-1234-123456789012"


class DDB:
    def __init__(self, status="active", revision=7, error=False):
        self.status, self.revision, self.error = status, revision, error
        self.writes = []
    def get_item(self, **kw):
        return {"Item":{"revision":{"N":str(self.revision)}, "record":{"S":json.dumps({
            "tenant_id":"usr-good", "demo":False, "status":self.status,
            "payer_id":"cus_hidden", "key_hashes":["private-hash"]})}}}
    def transact_write_items(self, **kw):
        if self.error: raise RuntimeError("Audit transaction refused")
        self.writes.append(kw)


def manager(db):
    return AdminTenantActions(db,"tenants","admin_audit",audit_key=b"k"*32,
        clock=lambda: datetime(2026,11,19,tzinfo=timezone.utc))


def payload(**kwargs):
    return dict(tenant_id="usr-good",expected_revision=7,status="disabled",
        confirmation="usr-good",reason="Operator verified account suspension",
        actor=SUB,idempotency_key="change123456789123",**kwargs)


def test_atomic_audit_and_tenant_update_only_when_confirmed():
    db=DDB(); result=manager(db).change_status(**payload())
    assert result["status"] == "disabled" and result["revision"] == 8
    assert len(db.writes) == 1
    ops=db.writes[0]["TransactItems"]
    assert len(ops)==2 and ops[0]["Update"]["TableName"]=="tenants"
    assert ops[1]["Put"]["TableName"]=="admin_audit"
    assert "attribute_not_exists(event_id)" == ops[1]["Put"]["ConditionExpression"]
    assert "#revision = :prior" in ops[0]["Update"]["ConditionExpression"]
    assert "cus_hidden" not in json.dumps(ops[1]) and "private-hash" not in json.dumps(ops[1])
    assert ops[1]["Put"]["Item"]["old_status"]["S"] == "active"


@pytest.mark.parametrize("change",[
    {"confirmation":"wrong"},{"status":"active"},{"reason":"short"},
    {"expected_revision":True},{"idempotency_key":"1"},
    {"tenant_id":"../wrong"},{"actor":""},
])
def test_invalid_or_unconfirmed_change_has_no_writes(change):
    db=DDB(); params=payload();params.update(change)
    with pytest.raises((ValueError, AdminActionConflict)):
        manager(db).change_status(**params)
    assert not db.writes


def test_revision_conflict_and_audit_outage_are_fail_closed():
    db=DDB(revision=8)
    with pytest.raises(AdminActionConflict): manager(db).change_status(**payload())
    assert not db.writes
    db=DDB(error=True)
    with pytest.raises(AdminActionUnavailable): manager(db).change_status(**payload())
    assert not db.writes


def test_action_route_rejects_unauthenticated_or_cross_origin_and_enforces_audit():
    db=DDB(); app=FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(SUB,frozenset({"owner"}),True) if token=="owner" else None,
        admin_mutator=manager(db)))
    client=TestClient(app)
    url="/v1/admin/tenants/usr-good/status"
    body={k:v for k,v in payload().items() if k not in {"tenant_id","actor"}}
    assert client.post(url,json=body).status_code==401
    assert client.post(url,json=body,headers={"Authorization":"Bearer owner"}).status_code==403
    assert not db.writes
    success=client.post(url,json=body,headers={"Authorization":"Bearer owner","Origin":"https://guardoath.com"})
    assert success.status_code==200 and len(db.writes)==1


def test_write_route_not_mounted_without_audit_capability():
    app=FastAPI();app.include_router(create_admin_router())
    assert not any("/status" in route.path for route in all_routes(app.routes))


def test_rule_change_preserves_stage_check_and_uses_atomic_audit():
    db=DDB()
    from types import SimpleNamespace
    # A stand-in for TenantRecord.model_validate only in this isolated test.
    validate=lambda doc: SimpleNamespace(model_dump=lambda:doc)
    result=manager(db).change_rule_action(
        tenant_id="usr-good",expected_revision=7,stage="input",rule_id="pii.email",
        action="block",confirmation="usr-good",reason="Owner approved targeted rule setting",
        actor=SUB,idempotency_key="ruleaction00000001",validate_record=validate,
        rule_exists=lambda rid:rid=="pii.email")
    assert result["action"]=="block" and result["revision"]==8
    update,audit=db.writes[0]["TransactItems"]
    doc=json.loads(update["Update"]["ExpressionAttributeValues"][":record"]["S"])
    assert doc["input_rule_actions"]=={"pii.email":"block"}
    assert "output_rule_actions" not in doc
    assert doc["status"]=="active"
    assert audit["Put"]["Item"]["rule_id"]["S"]=="pii.email"
    assert audit["Put"]["Item"]["prior_revision"]["N"] == "7"
    assert audit["Put"]["Item"]["new_revision"]["N"] == "8"
    assert audit["Put"]["Item"]["old_action"]["S"] == "same_as_check"
    assert audit["Put"]["Item"]["new_action"]["S"] == "block"
    assert "cus_hidden" not in json.dumps(audit)


def test_rule_change_refuses_unknown_rule_without_any_write():
    db=DDB()
    with pytest.raises(ValueError):
        manager(db).change_rule_action(
            tenant_id="usr-good",expected_revision=7,stage="input",rule_id="madeup.fake",
            action="block",confirmation="usr-good",reason="Owner approved targeted rule setting",
            actor=SUB,idempotency_key="ruleaction00000001",validate_record=lambda doc:doc,
            rule_exists=lambda rid:False)
    assert not db.writes


def test_cost_cap_cannot_increase_usage_and_is_automatically_blocking():
    db=DDB()
    from types import SimpleNamespace
    original=db.get_item
    def read(**kw):
        response=original(**kw)
        record=json.loads(response["Item"]["record"]["S"])
        record["monthly_allowance"]=10000
        record["overage"]="allow_and_record"
        response["Item"]["record"]["S"]=json.dumps(record)
        return response
    db.get_item=read
    val=lambda doc:SimpleNamespace(model_dump=lambda:doc)
    args=dict(tenant_id="usr-good",expected_revision=7,confirmation="usr-good",
              reason="Owner limited budget for tenant",actor=SUB,idempotency_key="cap123456789101112",
              validate_record=val)
    with pytest.raises(AdminActionConflict):
        manager(db).cap_monthly_allowance(new_limit=12000,**args)
    assert db.writes==[]
    result=manager(db).cap_monthly_allowance(new_limit=7000,**args)
    assert result["overage"]=="block" and result["revision"]==8
    ops=db.writes[0]["TransactItems"]
    updated=json.loads(ops[0]["Update"]["ExpressionAttributeValues"][":record"]["S"])
    assert updated["monthly_allowance"]==7000 and updated["overage"]=="block"
    assert ops[1]["Put"]["Item"]["operation"]["S"]=="tenant.allowance_cap"
    assert ops[1]["Put"]["Item"]["prior_revision"]["N"] == "7"
    assert ops[1]["Put"]["Item"]["new_revision"]["N"] == "8"
    assert ops[1]["Put"]["Item"]["old_limit"]["N"] == "10000"
    assert ops[1]["Put"]["Item"]["new_limit"]["N"] == "7000"
