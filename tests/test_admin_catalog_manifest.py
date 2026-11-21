"""Reviewed, source-bound system metadata manifest with owner/MFA and audit acceptance."""
from copy import deepcopy
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guardoath.admin_audit import AdminSensitiveReadAuditor
from guardoath.admin_catalog_drafts import AdminCatalogDrafts
from guardoath.admin_catalog_manifest import (
    CatalogManifestConflict, CatalogManifestUnavailable, reviewed_metadata_manifest)
from guardoath.admin_catalog_preview import preview_rule_description, _fingerprint
from guardoath.admin_console import VerifiedAdmin, create_admin_router

OWNER = "12345678-1234-1234-1234-123456789012"
OTHER = "87654321-4321-4321-4321-210987654321"
SOURCE = {"registry_version":"3","domains":[
    {"domain_id":"privacy","name":"Privacy","status":"active","controls":[
        {"control_id":"pii","name":"PII","stages":["input","output"],"rules":[
            {"rule_id":"pii.email","title":"Email rule","use_case":"Protect personal emails"},
            {"rule_id":"pii.phone","title":"Phone rule","use_case":"Protect phone numbers"}]}]},
    {"domain_id":"secrets","name":"Secrets","status":"active","controls":[
        {"control_id":"secrets","name":"Secrets","stages":["input","output"],"rules":[
            {"rule_id":"secret.key","title":"Key rule","use_case":"Protect API keys"}]}]}]}
PROPOSAL = {
    "expected_registry_version":"3", "proposed_registry_version":"4",
    "category_id":"privacy","check_id":"pii","rule_id":"pii.email",
    "title":"Revised email rule", "use_case":"Protect personal emails"}

class DB:
    def __init__(self):
        self.rows = {}
        self.writes = 0
        self.unavailable = False
    def put_item(self, **kw):
        if self.unavailable:
            raise RuntimeError("audit unavailable")
        ident = kw["Item"]["event_id"]["S"]
        if ident in self.rows:
            raise RuntimeError("conditional conflict")
        self.rows[ident] = deepcopy(kw["Item"])
        self.writes += 1
    def get_item(self, **kw):
        if self.unavailable:
            raise RuntimeError("audit unavailable")
        row = self.rows.get(kw["Key"]["event_id"]["S"])
        return {"Item": deepcopy(row)} if row is not None else {}

def setup():
    source, db = deepcopy(SOURCE), DB()
    drafts = AdminCatalogDrafts(db, "admin-audit", b"k"*32,
        clock=lambda: datetime(2026,11,20,tzinfo=timezone.utc))
    preview = preview_rule_description(lambda: source, **PROPOSAL)
    draft = drafts.save(actor=OWNER, nonce="draft-safe-key-123456",
        reason="Review documented catalog metadata", preview=preview,
        title=PROPOSAL["title"], use_case=PROPOSAL["use_case"])
    review = drafts.review(actor=OWNER,draft_id=draft["draft_id"],
        nonce="review-safe-key-123456", reason="Approved review evidence only",
        confirmation="REVIEW "+draft["draft_id"][-8:],
        baseline_sha256=draft["baseline_sha256"],
        proposal_sha256=draft["proposal_sha256"])
    return source, db, drafts, draft, review

def test_manifest_uses_one_snapshot_and_is_a_stable_nonpublishing_projection():
    source, db, drafts, draft, review = setup()
    before = deepcopy(source)
    calls = []
    def provider():
        calls.append(1)
        return source
    result = reviewed_metadata_manifest(provider,draft=draft,review=review)
    assert calls == [1]
    assert result["status"] == "metadata_manifest_preview_only"
    assert result["registry_version"] == "3" and result["candidate_registry_version"] == "4"
    assert result["changed_fields"] == ["title"]
    assert result["category_count"] == 2 and result["check_count"] == 2 and result["rule_count"] == 3
    assert result["reviewed_source_sha256"] == draft["baseline_sha256"]
    assert result["rollback_source_sha256"] == draft["baseline_sha256"]
    assert result["candidate_metadata_sha256"] != draft["baseline_sha256"]
    assert result["published"] is False and result["ready_to_publish"] is False
    assert result["acceptance_tests_run"] is False
    assert result["tenant_policies_changed"] is False and result["detector_behavior_changed"] is False
    assert result == reviewed_metadata_manifest(lambda:source,draft=draft,review=review)
    assert source == before and db.writes == 2

@pytest.mark.parametrize("change", [
    lambda s:s["domains"][0]["controls"][0]["rules"][0].update({"title":"Other title"}),
    lambda s:s.update({"registry_version":"4"}),
    lambda s:s["domains"][1]["controls"][0]["rules"][0].update({"title":"Other secret title"}),
])
def test_stale_catalog_does_not_generate_candidate(change):
    source, _, _, draft, review = setup()
    change(source)
    with pytest.raises(CatalogManifestConflict):
        reviewed_metadata_manifest(lambda:source,draft=draft,review=review)

def test_rejects_forged_reviews_and_invalid_proposals_without_publishing():
    source, db, drafts, draft, review = setup()
    for bad_review in [
        {**review, "proposal_sha256":"0"*64},
        {**review, "draft_id":"0"*64},
        {**review, "published":True},
        {**review, "status":"unreviewed"},
    ]:
        with pytest.raises(CatalogManifestUnavailable):
            reviewed_metadata_manifest(lambda:source,draft=draft,review=bad_review)
    with pytest.raises(CatalogManifestConflict):
        reviewed_metadata_manifest(lambda:source,draft={**draft,"changes":{"title":{"before":"Wrong","after":"Bad"}}},review=review)
    with pytest.raises(CatalogManifestUnavailable):
        reviewed_metadata_manifest(lambda: (_ for _ in ()).throw(RuntimeError("offline")),
            draft=draft,review=review)
    assert db.writes == 2 and source == SOURCE

def test_metadata_manifest_route_owner_only_audited_and_fails_closed():
    source, db, drafts, draft, review = setup()
    calls = []
    def provider():
        calls.append(1)
        return source
    def auth(token):
        if token == "owner": return VerifiedAdmin(OWNER, frozenset({"owner"}), True)
        if token == "other": return VerifiedAdmin(OTHER, frozenset({"owner"}), True)
        if token == "no-mfa": return VerifiedAdmin(OWNER, frozenset({"owner"}), False)
        return None
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=auth, catalog_provider=provider,
        catalog_drafts=drafts,
        read_auditor=AdminSensitiveReadAuditor(
            db,"admin-audit",signing_key=b"k"*32),
        require_persistent_read_audit=True))
    client = TestClient(app)
    path = "/v1/admin/catalog/reviews/"+review["review_id"]+"/manifest"
    assert client.get(path).status_code == 401
    assert client.get(path,headers={"X-GuardOath-Key":"owner"}).status_code == 401
    assert client.get(path,headers={"X-GuardOath-Admin-Session":"no-mfa"}).status_code == 403
    assert client.get(path,headers={"X-GuardOath-Admin-Session":"other"}).status_code == 503
    # A valid independent owner's denied lookup still emits one signed read intent.
    assert db.writes == 3
    malformed = "/v1/admin/catalog/reviews/" + review["review_id"] + "x/manifest"
    assert client.get(malformed,headers={"X-GuardOath-Admin-Session":"owner"}).status_code == 422
    assert db.writes == 3  # malformed request never reads or audits protected data
    assert calls == []
    headers={"X-GuardOath-Admin-Session":"owner"}
    ok=client.get(path,headers=headers)
    assert ok.status_code == 200,ok.text
    result=ok.json()
    assert result["review_id"] == review["review_id"]
    assert result["draft_id"] == draft["draft_id"]
    assert len(result["read_audit_event_id"]) == 64
    assert calls == [1] and db.writes == 4  # signed owner audit in addition to denied other-owner attempt
    assert SOURCE == source
    source["domains"][0]["controls"][0]["rules"][0]["title"] = "Changed by release"
    assert client.get(path,headers=headers).status_code == 409
    before=len(calls)
    db.unavailable=True
    assert client.get(path,headers=headers).status_code == 503
    assert len(calls)==before

def test_missing_or_tampered_signed_review_is_never_materialized():
    source, db, drafts, draft, review = setup()
    app=FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(OWNER,frozenset({"owner"}),True)
        if token=="owner" else None,
        catalog_provider=lambda:source,catalog_drafts=drafts))
    client=TestClient(app)
    base="/v1/admin/catalog/reviews/"
    headers={"X-GuardOath-Admin-Session":"owner"}
    assert client.get(base+"f"*64+"/manifest",headers=headers).status_code==404
    db.rows[review["review_id"]]["document"]["S"]="{}"
    assert client.get(base+review["review_id"]+"/manifest",headers=headers).status_code==503
    assert db.writes==2
