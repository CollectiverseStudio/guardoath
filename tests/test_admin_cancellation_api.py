"""Owner/MFA/origin protection and actual API/state integration for cancellation."""
import copy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from admin_cancellation_support import ACTOR, TENANT, KEY, request, review, service
from guardoath import state_contracts
from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor
from guardoath.admin_console import VerifiedAdmin, create_admin_router

PREFIX = "/v1/admin/subscriptions/" + TENANT
HEADERS = {"X-GuardOath-Admin-Session":"owner", "Origin":"https://guardoath.com"}


def client(actions, db, verifier=None):
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=verifier or (
        lambda token:VerifiedAdmin(ACTOR,frozenset({"owner"}),True) if token == "owner" else None),
        billing_cancellation=actions,
        read_auditor=AdminSensitiveReadAuditor(db,"audit_table",signing_key=KEY),
        require_persistent_read_audit=True))
    return TestClient(app)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_CANCELLATION_STATE",True)


def test_actual_owner_api_preview_schedule_and_historical_read_are_audited(enabled):
    actions, db, boundary = service()
    browser = client(actions,db)
    before = copy.deepcopy(db.get_item(TableName="tenant_table",Key={"tenant_id":{"S":TENANT}}))
    first = browser.post(PREFIX+"/cancellation/preview",headers=HEADERS,json={"expected_revision":3})
    assert first.status_code == 200
    payload = request(first.json()["cancellation"])
    del payload["actor"]; del payload["tenant_id"]
    second = browser.post(PREFIX+"/cancel-at-period-end",headers=HEADERS,json=payload)
    assert second.status_code == 200 and second.json()["cancellation"]["status"] == "verified"
    third = browser.post(PREFIX+"/cancellation/receipt",headers=HEADERS,json={"idempotency_key":payload["idempotency_key"]})
    assert third.status_code == 200 and third.json()["cancellation"] == second.json()["cancellation"]
    reader = AdminAuditReader(db,"audit_table",verify_key=KEY)
    for response,resource in ((first,"stripe.subscription"),(second,"stripe.subscription"),(third,"audit.receipt")):
        assert response.headers["cache-control"] == "no-store"
        receipt = reader.read(response.json()["read_audit_event_id"])
        assert receipt["integrity_status"] == "signed_verified"
        assert receipt["change"] == {"field":"protected_read", "resource":resource}
    assert len(boundary.posts) == 1
    assert db.get_item(TableName="tenant_table",Key={"tenant_id":{"S":TENANT}}) == before


@pytest.mark.parametrize("role,mfa", [("customer",True),("billing_admin",True),("support",True),("owner",False)])
@pytest.mark.parametrize("endpoint", ["/cancellation/preview","/cancel-at-period-end","/cancellation/receipt"])
def test_non_owner_or_missing_mfa_denied_before_any_financial_boundary(enabled,role,mfa,endpoint):
    actions, db, boundary = service()
    browser = client(actions,db,lambda _:VerifiedAdmin(ACTOR,frozenset({role}),mfa))
    response = browser.post(PREFIX+endpoint,headers=HEADERS,json={})
    assert response.status_code == 403 and not boundary.calls and not db.audit_items()


@pytest.mark.parametrize("headers", [{}, {"X-GuardOath-Key":"demo"},
    {"X-GuardOath-Key":"customer-key"}, {"X-GuardOath-Admin-Session":"forged"}])
@pytest.mark.parametrize("endpoint", ["/cancellation/preview","/cancel-at-period-end","/cancellation/receipt"])
def test_missing_or_unverified_identity_never_gets_financial_reads(enabled,headers,endpoint):
    actions, db, boundary = service()
    response = client(actions,db).post(PREFIX+endpoint,headers={**headers,"Origin":"https://guardoath.com"},json={})
    assert response.status_code == 401 and not boundary.calls and not db.audit_items()


@pytest.mark.parametrize("origin", [None,"https://foreign.invalid","http://localhost:8780"])
@pytest.mark.parametrize("endpoint", ["/cancellation/preview","/cancel-at-period-end","/cancellation/receipt"])
def test_every_cancellation_post_requires_same_origin(enabled,origin,endpoint):
    actions, db, boundary = service()
    headers = {k:v for k,v in HEADERS.items() if k != "Origin"}
    if origin is not None: headers["Origin"] = origin
    payload = {"expected_revision":3} if endpoint.endswith("preview") else {"idempotency_key":"receipt-reference-123456789"}
    if endpoint == "/cancel-at-period-end":
        payload.update(expected_revision=3,confirmation="SCHEDULE CANCELLATION "+TENANT,
            reason="Verify wrong origin cannot mutate",preview_reference="sc1."+"a"*50)
    response = client(actions,db).post(PREFIX+endpoint,headers=headers,json=payload)
    assert response.status_code == 403 and not boundary.calls and not db.audit_items()


def test_audit_outage_prevents_preview_tenant_and_provider_read(enabled,monkeypatch):
    actions, db, boundary = service()
    db.fail_audit = True
    reads = []
    monkeypatch.setattr(db,"get_item",lambda **kwargs:reads.append(kwargs))
    response = client(actions,db).post(PREFIX+"/cancellation/preview",headers=HEADERS,json={"expected_revision":3})
    assert response.status_code == 503 and not reads and not boundary.calls


@pytest.mark.parametrize("extra", [{"subscription_id":"sub_other"},{"price":"price_other"},
    {"url":"https://foreign.invalid"},{"cancel_at":1},{"amount":100},{"expected_revision":True}])
def test_browser_cannot_choose_provider_target_or_mutation_parameters(enabled,extra):
    actions, db, boundary = service()
    response = client(actions,db).post(PREFIX+"/cancellation/preview",headers=HEADERS,json={"expected_revision":3,**extra})
    assert response.status_code == 422 and not boundary.calls and not db.audit_items()


def test_provider_failure_has_fixed_private_safe_error(enabled):
    actions, db, boundary = service()
    boundary.before = lambda req:(_ for _ in ()).throw(RuntimeError("rk_live_private cus_private provider body"))
    response = client(actions,db).post(PREFIX+"/cancellation/preview",headers=HEADERS,json={"expected_revision":3})
    assert response.status_code == 503
    assert "private" not in response.text and "rk_" not in response.text
    assert len(db.audit_items()) == 1


def test_unconfirmed_action_never_claims_target_or_calls_provider(enabled):
    actions, db, boundary = service()
    payload = request(review(actions))
    del payload["actor"]; del payload["tenant_id"]
    payload["confirmation"] = "SCHEDULE CANCELLATION usr-other"
    before = len(boundary.calls)
    response = client(actions,db).post(PREFIX+"/cancel-at-period-end",headers=HEADERS,json=payload)
    assert response.status_code == 422 and len(boundary.calls) == before
    assert all("financial_operation" not in row for row in db.audit_items())


@pytest.mark.parametrize("remaining,expected",[(15000,200),(3000,503),(0,503),(True,503),(float("nan"),503)])
def test_api_uses_trusted_lambda_remaining_time_and_never_accepts_browser_budget(enabled,remaining,expected):
    from types import SimpleNamespace
    actions,db,boundary=service()
    payload=request(review(actions))
    del payload["actor"];del payload["tenant_id"]
    browser=client(actions,db)
    @browser.app.middleware("http")
    async def context(request,call_next):
        request.scope["aws.context"]=SimpleNamespace(get_remaining_time_in_millis=lambda:remaining)
        return await call_next(request)
    injected=browser.post(PREFIX+"/cancel-at-period-end",headers=HEADERS,json={**payload,"execution_budget_seconds":8})
    assert injected.status_code==422 and not boundary.posts
    result=browser.post(PREFIX+"/cancel-at-period-end",headers=HEADERS,json=payload)
    assert result.status_code==expected
    assert len(boundary.posts)==(1 if expected==200 else 0)
    if expected!=200:
        assert all("financial_operation" not in row for row in db.audit_items())
