"""Actual signed owner request/API/state recovery; no live provider claims."""
from copy import deepcopy
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from admin_test_support import AdminTestDynamo
from guardoath import state_contracts
from guardoath.admin_audit import AdminSensitiveReadAuditor
from guardoath.admin_catalog_drafts import AdminCatalogDrafts
from guardoath.admin_catalog_manifest import reviewed_metadata_manifest
from guardoath.admin_catalog_preview import preview_rule_description
from guardoath.admin_catalog_publication import AdminCatalogPublications, source_plan_from_manifest
from guardoath.admin_catalog_publication_state import (
    DynamoCatalogPublicationState, PublicationConflict, PublicationUnavailable, document_hash)
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.catalog_source_release import candidate_release

OWNER="12345678-1234-1234-1234-123456789012"
OTHER="87654321-4321-4321-4321-210987654321"
KEY=b"publication-test-audit-key-not-production-0123456789"
SOURCE={"registry_version":"3","domains":[{"domain_id":"privacy","name":"Privacy","status":"active",
    "controls":[{"control_id":"pii","name":"Personal identifiers","stages":["input","output"],
        "rules":[{"rule_id":"pii.email","title":"Email rule",
                  "use_case":"Turn this on to protect personal emails"}]}]}]}
BASE={"base_registry_version":"3","releases":[]}


@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_CATALOG_PUBLICATION_STATE",True)
    path=tmp_path/"publication.sqlite3"
    db=AdminTestDynamo(path)
    source,history=deepcopy(SOURCE),deepcopy(BASE)
    calls=[]
    drafts=AdminCatalogDrafts(db,"audit_table",KEY,
        clock=lambda:datetime(2026,11,24,tzinfo=timezone.utc))
    state=DynamoCatalogPublicationState(db,"audit_table",KEY)
    def catalog():
        calls.append("catalog")
        return source
    service=AdminCatalogPublications(state=state,drafts=drafts,catalog_provider=catalog,
        history_provider=lambda:history,source_provider=lambda:"a"*40,clock=lambda:1795478400)
    result={"path":path,"db":db,"source":source,"history":history,"drafts":drafts,
            "state":state,"service":service,"calls":calls}
    draft,review=saved_review(result,"Clearer email rule",nonce="initial")
    result.update(draft=draft,review=review)
    yield result
    db.close()


def saved_review(env,title,nonce):
    current=env["source"]["registry_version"]
    proposal={"expected_registry_version":current,"proposed_registry_version":str(int(current)+1),
        "category_id":"privacy","check_id":"pii","rule_id":"pii.email","title":title,
        "use_case":"Turn this on to protect personal emails"}
    preview=preview_rule_description(lambda:env["source"],**proposal)
    draft=env["drafts"].save(actor=OWNER,nonce="draft-reference-"+nonce,
        reason="Clarify the owner catalog description",preview=preview,title=title,use_case=proposal["use_case"])
    review=env["drafts"].review(actor=OWNER,draft_id=draft["draft_id"],nonce="review-reference-"+nonce,
        reason="Review exact descriptive source change",confirmation="REVIEW "+draft["draft_id"][-8:],
        baseline_sha256=draft["baseline_sha256"],proposal_sha256=draft["proposal_sha256"])
    return draft,review


def body(env,**updates):
    preview=env["service"].preview(actor=OWNER,review_id=env["review"]["review_id"],operation="publish_metadata")
    return {"review_id":preview["review_id"],"operation":"publish_metadata",
        "expected_source_sha":preview["base_source_sha"],"expected_control_hash":preview["expected_control_hash"],
        "reason":"Prepare the reviewed description release","action_reference":"publication-action-reference-001",
        "confirmation":preview["confirmation"],**updates}


def client(env):
    def verify(token):
        if token in ("owner","other"):
            return VerifiedAdmin(OWNER if token=="owner" else OTHER,frozenset({"owner"}),True)
        if token=="customer": return VerifiedAdmin(OWNER,frozenset({"customer"}),True)
        if token=="no-mfa": return VerifiedAdmin(OWNER,frozenset({"owner"}),False)
        return None
    app=FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=verify,catalog_provider=lambda:env["source"],
        catalog_drafts=env["drafts"],catalog_publications=env["service"],
        read_auditor=AdminSensitiveReadAuditor(env["db"],"audit_table",signing_key=KEY),
        require_persistent_read_audit=True))
    return TestClient(app,base_url="http://127.0.0.1:8780")


HEADERS={"X-GuardOath-Admin-Session":"owner","Origin":"http://127.0.0.1:8780"}


def test_preview_requires_separate_confirmation_and_preserves_source(env):
    before=deepcopy(env["source"])
    preview=env["service"].preview(actor=OWNER,review_id=env["review"]["review_id"],operation="publish_metadata")
    assert preview["status"]=="publication_preview" and preview["writer_enabled"] is True
    assert preview["control_available"] is True and preview["expected_control_hash"]=="0"*64
    assert preview["base_source_sha"]=="a"*40 and preview["confirmation"].startswith("PUBLISH ")
    assert env["state"].read_control() is None and env["db"].transactions==0
    assert env["source"]==before
    for name in ("published","remote_published","deployed","ci_verified","tenant_policies_changed","detector_behavior_changed"):
        assert preview[name] is False


def test_request_reopens_signed_records_and_recovers_after_source_changes(env):
    request=body(env)
    receipt=env["service"].request(actor=OWNER,**request)
    assert receipt["status"]=="requested" and receipt["source_preparation"] is None
    assert receipt["action_reference"]==request["action_reference"]
    assert env["db"].transactions==1
    env["service"].catalog_provider=lambda:pytest.fail("Historical recovery must not read current catalog")
    env["service"].source_provider=lambda:pytest.fail("Historical recovery must not need a build manifest")
    assert env["service"].request(actor=OWNER,**request)==receipt
    reopened=AdminTestDynamo(env["path"])
    try:
        state=DynamoCatalogPublicationState(reopened,"audit_table",KEY)
        intent,prepared=state.history(receipt["request_id"])
        assert prepared is None and intent["actor_sub"]==OWNER
        assert intent["review_sha256"]==document_hash(env["review"])
        assert intent["draft_sha256"]==document_hash(env["draft"])
        assert intent["plan_sha256"]==document_hash(intent["plan"])
    finally: reopened.close()
    assert env["service"].recover(actor=OWNER,action_reference=request["action_reference"])==receipt
    assert env["service"].inspect(actor=OTHER,request_id=receipt["request_id"]) is None
    assert env["service"].recover(actor=OTHER,action_reference=request["action_reference"]) is None
    assert env["db"].transactions==1


@pytest.mark.parametrize("change",[
    {"reason":"A different request sharing its reference"},
    {"operation":"reverse_latest","confirmation":None},
    {"expected_source_sha":"b"*40},{"expected_control_hash":"f"*64},
])
def test_replay_changed_payload_never_becomes_another_request(env,change):
    request=body(env)
    env["service"].request(actor=OWNER,**request)
    if change.get("confirmation","absent") is None:
        change={**change,"confirmation":"REVERSE "+request["review_id"][-8:]}
    with pytest.raises(PublicationConflict):
        env["service"].request(actor=OWNER,**{**request,**change})
    assert env["db"].transactions==1


@pytest.mark.parametrize("change",[
    {"confirmation":"PUBLISH 00000000"},{"reason":"short"},{"reason":" Invalid leading space"},
    {"reason":"Invisible\u200brelease reason"},{"action_reference":"short"},
    {"operation":"run_code"},{"expected_source_sha":"a"*39},{"expected_control_hash":True},
])
def test_invalid_confirmation_never_persists_intent(env,change):
    request=body(env)
    with pytest.raises(ValueError): env["service"].request(actor=OWNER,**{**request,**change})
    assert env["state"].read_control() is None


@pytest.mark.parametrize("change",["source","catalog","review","draft","history"])
def test_stale_or_unverified_evidence_never_acquires_control(env,change):
    request=body(env)
    if change=="source": env["service"].source_provider=lambda:"b"*40
    elif change=="catalog": env["source"]["domains"][0]["controls"][0]["rules"][0]["title"]="Another source title"
    elif change=="history": env["history"]["releases"]=[{"invalid":True}]
    else:
        identifier=env[change][change+"_id"]
        item=env["db"].get_item(TableName="audit_table",Key={"event_id":{"S":identifier}})["Item"]
        item["integrity_mac"]={"S":"0"*64}
        env["db"].seed("audit_table",item)
    with pytest.raises((PublicationConflict,PublicationUnavailable)):
        env["service"].request(actor=OWNER,**request)
    assert env["state"].read_control() is None


def test_missing_source_manifest_blocks_preview_but_not_historical_reader(env,monkeypatch):
    request=body(env)
    receipt=env["service"].request(actor=OWNER,**request)
    env["service"].source_provider=lambda:None
    with pytest.raises(PublicationUnavailable):
        env["service"].preview(actor=OWNER,review_id=request["review_id"],operation="publish_metadata")
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_CATALOG_PUBLICATION_STATE",False)
    app=client(env)
    assert app.post("/v1/admin/catalog/publications",headers=HEADERS,json=request).status_code==404
    recovered=app.post("/v1/admin/catalog/publications/recover",headers=HEADERS,
        json={"action_reference":request["action_reference"]})
    assert recovered.status_code==200 and recovered.json()["request_id"]==receipt["request_id"]
    features=app.get("/v1/admin/features",headers=HEADERS).json()
    assert features["catalog_publication_history"] is True
    assert features["catalog_publication_requests"] is False and features["rule_publishing"] is False
    with pytest.raises(PublicationUnavailable): env["service"].request(actor=OWNER,**request)


def test_single_global_control_and_lost_response_recovery(env):
    request=body(env)
    env["db"].lose_transaction_response=True
    receipt=env["service"].request(actor=OWNER,**request)
    assert receipt["status"]=="requested" and env["db"].transactions==1
    preview=env["service"].preview(actor=OWNER,review_id=request["review_id"],operation="publish_metadata")
    assert preview["control_available"] is False
    with pytest.raises(PublicationConflict):
        env["service"].request(actor=OWNER,**{**request,"action_reference":"another-action-reference-002"})
    assert env["db"].transactions==1


def test_signed_worker_stages_are_not_publication_or_deployment(env):
    request=body(env)
    receipt=env["service"].request(actor=OWNER,**request)
    claim=env["state"].claim(receipt["request_id"])
    assert env["service"].recover(actor=OWNER,action_reference=request["action_reference"])["status"]=="preparing"
    env["state"].complete_preparation(receipt["request_id"],claim,{"base_source_sha":"a"*40,
        "candidate_source_sha":"b"*40,"history_sha256":"c"*64,
        "candidate_metadata_sha256":receipt["candidate_metadata_sha256"],"candidate_registry_version":"4",
        "recorded_at":1795478401})
    prepared=env["service"].inspect(actor=OWNER,request_id=receipt["request_id"])
    assert prepared["status"]=="source_prepared" and prepared["source_preparation"]["candidate_source_sha"]=="b"*40
    assert all(prepared[name] is False for name in ("remote_published","ci_verified","deployed","published"))


def test_reverse_latest_requires_a_fresh_exact_inverse_review(env):
    manifest=reviewed_metadata_manifest(lambda:env["source"],draft=env["draft"],review=env["review"])
    history=candidate_release(source_plan_from_manifest(manifest),lambda:env["source"],env["history"])
    env["history"].update(history)
    env["source"]["registry_version"]="4"
    env["source"]["domains"][0]["controls"][0]["rules"][0]["title"]="Clearer email rule"
    _,wrong=saved_review(env,"Another email title","wrong-inverse")
    with pytest.raises(PublicationConflict):
        env["service"].preview(actor=OWNER,review_id=wrong["review_id"],operation="reverse_latest")
    _,review=saved_review(env,"Email rule","exact-inverse")
    preview=env["service"].preview(actor=OWNER,review_id=review["review_id"],operation="reverse_latest")
    assert preview["registry_version"]=="4" and preview["candidate_registry_version"]=="5"
    assert preview["confirmation"]=="REVERSE "+review["review_id"][-8:]
    assert preview["changed_metadata"]["title"]=={"before":"Clearer email rule","after":"Email rule"}


@pytest.mark.parametrize("token,status",[(None,401),("customer",403),("no-mfa",403),("forged",401)])
def test_routes_deny_unverified_owners_before_catalog_or_state_reads(env,token,status):
    app=client(env)
    headers={"Origin":"http://127.0.0.1:8780"}
    if token: headers["X-GuardOath-Admin-Session"]=token
    paths=[("/v1/admin/catalog/publications/preview",{"review_id":env["review"]["review_id"],"operation":"publish_metadata"}),
           ("/v1/admin/catalog/publications/recover",{"action_reference":"publication-action-reference-001"})]
    before=len(env["db"].audit_items())
    env["calls"].clear()
    for path,payload in paths: assert app.post(path,headers=headers,json=payload).status_code==status
    assert env["calls"]==[] and len(env["db"].audit_items())==before


def test_route_origin_audit_exact_fields_and_independent_history(env):
    app=client(env)
    request=body(env)
    endpoint="/v1/admin/catalog/publications"
    assert app.post(endpoint,headers={**HEADERS,"Origin":"https://untrusted.invalid"},json=request).status_code==403
    assert app.post(endpoint,headers=HEADERS,json={**request,"run_command":"no"}).status_code==422
    env["calls"].clear()
    env["db"].fail_audit=True
    assert app.post(endpoint,headers=HEADERS,json=request).status_code==503
    assert env["calls"]==[] and env["db"].transactions==0
    env["db"].fail_audit=False
    response=app.post(endpoint,headers=HEADERS,json=request)
    assert response.status_code==200,response.text
    result=response.json()
    assert result["status"]=="requested" and len(result["read_audit_event_id"])==64
    assert response.headers["cache-control"]=="no-store"
    assert app.get(endpoint+"/"+result["request_id"],headers=HEADERS).json()["intent_sha256"]==result["intent_sha256"]
    assert app.get(endpoint+"/"+result["request_id"],headers={**HEADERS,"X-GuardOath-Admin-Session":"other"}).status_code==404
    assert app.get(endpoint+"/wrong",headers=HEADERS).status_code==422
    assert env["db"].transactions==1


def test_mismatched_audit_store_is_rejected_at_router_construction(env):
    wrong=AdminTestDynamo()
    try:
        with pytest.raises(ValueError,match="bound signed"):
            create_admin_router(catalog_publications=env["service"],catalog_drafts=env["drafts"],
                read_auditor=AdminSensitiveReadAuditor(wrong,"audit_table",signing_key=KEY))
    finally: wrong.close()
