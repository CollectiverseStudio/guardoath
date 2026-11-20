"""Owner-only immutable metadata draft persistence, authorization and source parity."""
from copy import deepcopy
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guardoath.admin_catalog_drafts import AdminCatalogDrafts, DraftConflict, DraftUnavailable
from guardoath.admin_catalog_preview import preview_rule_description
from guardoath.admin_console import VerifiedAdmin, create_admin_router


OWNER = "12345678-1234-1234-1234-123456789012"
OTHER = "87654321-4321-4321-4321-210987654321"
REGISTRY = {"registry_version": "3", "domains": [
    {"domain_id": "privacy", "name": "Privacy", "status": "active", "controls": [
        {"control_id": "pii", "name": "PII", "stages": ["input", "output"], "rules": [
            {"rule_id": "pii.email", "title": "Email rule",
             "use_case": "Protect personal email addresses"}]}]}]}
PROPOSAL = {"expected_registry_version": "3", "proposed_registry_version": "4",
            "category_id": "privacy", "check_id": "pii", "rule_id": "pii.email",
            "title": "Improved email rule",
            "use_case": "Protect personal email addresses"}


class Database:
    def __init__(self):
        self.items = {}
        self.writes = 0
        self.fail_write = False
        self.fail_read = False

    def put_item(self, *, TableName, Item, ConditionExpression):
        assert TableName == "admin-audit"
        assert ConditionExpression == "attribute_not_exists(event_id)"
        if self.fail_write:
            raise RuntimeError("Provider unavailable")
        key = Item["event_id"]["S"]
        if key in self.items:
            raise RuntimeError("ConditionalCheckFailed")
        self.items[key] = deepcopy(Item)
        self.writes += 1

    def get_item(self, *, TableName, Key, ConsistentRead):
        assert TableName == "admin-audit" and ConsistentRead is True
        if self.fail_read:
            raise RuntimeError("Provider unavailable")
        key = Key["event_id"]["S"]
        return {"Item": deepcopy(self.items[key])} if key in self.items else {}


def make_store():
    db = Database()
    return db, AdminCatalogDrafts(
        db, "admin-audit", b"k" * 32,
        clock=lambda: datetime(2026, 11, 20, tzinfo=timezone.utc))


def verified_preview():
    return preview_rule_description(lambda: deepcopy(REGISTRY), **PROPOSAL)


def saving_args():
    return {"actor": OWNER, "nonce": "replay-safe-unique-key-123",
            "reason": "Clarify this catalog validation use case",
            "preview": verified_preview(), "title": PROPOSAL["title"],
            "use_case": PROPOSAL["use_case"]}


def test_immutable_draft_replay_owner_isolation_and_no_registry_mutation():
    db, store = make_store()
    snapshot = deepcopy(REGISTRY)
    args = saving_args()
    first = store.save(**args)
    assert first["status"] == "draft_saved" and first["published"] is False
    assert first["tenant_policies_changed"] is False
    assert first["detector_behavior_changed"] is False
    assert first["baseline_sha256"] == args["preview"]["baseline_sha256"]
    assert first["proposal_sha256"] == args["preview"]["proposal_sha256"]
    assert len(first["draft_id"]) == 64 and db.writes == 1
    assert store.read(OWNER, first["draft_id"]) == first
    assert store.save(**args) == first and db.writes == 1
    with pytest.raises(DraftConflict):
        store.save(**{**args, "reason": "A distinctly different review reason"})
    with pytest.raises(DraftUnavailable):
        store.read(OTHER, first["draft_id"])
    assert REGISTRY == snapshot


def test_tampered_or_unavailable_audit_persistence_never_verifies_draft():
    db, store = make_store()
    args = saving_args()
    record = store.save(**args)
    key = record["draft_id"]
    db.items[key]["document"]["S"] = db.items[key]["document"]["S"].replace("Improved", "Injected")
    with pytest.raises(DraftUnavailable):
        store.read(OWNER, key)
    with pytest.raises(DraftUnavailable):
        store.save(**args)
    db, store = make_store()
    db.fail_write = True
    with pytest.raises(DraftUnavailable):
        store.save(**saving_args())
    assert db.writes == 0
    db.fail_read = True
    with pytest.raises(DraftUnavailable):
        store.read(OWNER, "a" * 64)


@pytest.mark.parametrize("change", [
    {"actor": "wrong"}, {"nonce": "bad"}, {"reason": "short"},
    {"title": "a"}, {"use_case": "b"},
    {"preview": {"status": "preview_only", "published": True}},
])
def test_invalid_draft_refuses_provider_write(change):
    db, store = make_store()
    with pytest.raises(ValueError):
        store.save(**{**saving_args(), **change})
    assert db.writes == 0


def test_owner_admin_api_denies_anonymous_cross_origin_and_stale_source():
    db, store = make_store()
    current = deepcopy(REGISTRY)
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: (
            VerifiedAdmin(OWNER, frozenset({"owner"}), True)
            if token == "verified-owner-token" else None),
        catalog_provider=lambda: current, catalog_drafts=store))
    client = TestClient(app)
    endpoint = "/v1/admin/catalog/drafts"
    body = {**PROPOSAL, "reason": "Clarify this catalog validation use case",
            "idempotency_key": "replay-safe-unique-key-123"}
    assert client.post(endpoint, json=body).status_code == 401
    assert client.post(endpoint, json=body,
                       headers={"Authorization": "Bearer customer-token"}).status_code == 401
    assert client.post(endpoint, json=body,
                       headers={"Authorization": "Bearer verified-owner-token"}).status_code == 403
    authorized = {"Authorization": "Bearer verified-owner-token",
                  "Origin": "https://guardoath.com"}
    saved = client.post(endpoint, json=body, headers=authorized)
    assert saved.status_code == 200, saved.text
    assert saved.json()["persisted"] is True and saved.json()["published"] is False
    draft_id = saved.json()["draft_id"]
    response = client.get(endpoint + "/" + draft_id, headers=authorized)
    assert response.status_code == 200
    assert response.json()["stale"] is False and response.json()["ready_to_publish"] is False
    assert db.writes == 1
    replay = client.post(endpoint, json=body, headers=authorized)
    assert replay.status_code == 200 and replay.json()["draft_id"] == draft_id
    current["domains"][0]["controls"][0]["rules"][0]["title"] = "Changed source title"
    result = client.get(endpoint + "/" + draft_id, headers=authorized)
    assert result.status_code == 200 and result.json()["stale"] is True
    # An identical key cannot approve a different published registry baseline.
    assert client.post(endpoint, json=body, headers=authorized).status_code == 409
    current["registry_version"] = "4"
    assert client.post(endpoint, json={**body, "idempotency_key": "another-unique-key-123"},
                       headers=authorized).status_code == 409
    assert db.writes == 1


def test_review_is_signed_immutable_idempotent_owner_scoped_and_never_publishes():
    db, store = make_store()
    draft = store.save(**saving_args())
    args = {"actor": OWNER, "draft_id": draft["draft_id"],
            "nonce": "review-safe-unique-key-123",
            "reason": "Verified current metadata and exact catalog fingerprint",
            "confirmation": "REVIEW " + draft["draft_id"][-8:],
            "baseline_sha256": draft["baseline_sha256"],
            "proposal_sha256": draft["proposal_sha256"]}
    baseline = deepcopy(REGISTRY)
    first = store.review(**args)
    assert first["status"] == "review_recorded"
    assert first["published"] is False and first["tenant_policies_changed"] is False
    assert first["detector_behavior_changed"] is False
    assert first["draft_id"] == draft["draft_id"] and len(first["review_id"]) == 64
    assert db.writes == 2 and REGISTRY == baseline
    assert store.read_review(OWNER, first["review_id"]) == first
    assert store.review(**args) == first and db.writes == 2
    with pytest.raises(DraftConflict):
        store.review(**{**args, "reason": "Different sufficiently long review reason"})
    with pytest.raises(DraftUnavailable):
        store.read_review(OTHER, first["review_id"])
    assert db.writes == 2
    db.items[first["review_id"]]["document"]["S"] = db.items[first["review_id"]]["document"]["S"].replace(
        "review_recorded", "review_injected")
    with pytest.raises(DraftUnavailable):
        store.read_review(OWNER, first["review_id"])
    with pytest.raises(DraftUnavailable):
        store.review(**args)


@pytest.mark.parametrize("change", [
    {"actor": "invalid-owner"},
    {"draft_id": "not-a-digest"},
    {"nonce": "short"},
    {"reason": "short"},
    {"confirmation": "REVIEW wrong-id"},
    {"baseline_sha256": "0" * 64},
    {"proposal_sha256": "f" * 64},
])
def test_review_invalid_or_mismatched_binding_never_writes(change):
    db, store = make_store()
    draft = store.save(**saving_args())
    args = {"actor": OWNER, "draft_id": draft["draft_id"],
            "nonce": "review-safe-unique-key-123",
            "reason": "Verified current metadata and exact catalog fingerprint",
            "confirmation": "REVIEW " + draft["draft_id"][-8:],
            "baseline_sha256": draft["baseline_sha256"],
            "proposal_sha256": draft["proposal_sha256"]}
    with pytest.raises((ValueError, DraftConflict, DraftUnavailable)):
        store.review(**{**args, **change})
    assert db.writes == 1


def test_review_requires_actual_persistence_and_verified_immutable_draft():
    db, store = make_store()
    draft = store.save(**saving_args())
    args = {"actor": OWNER, "draft_id": draft["draft_id"],
            "nonce": "review-safe-unique-key-123",
            "reason": "Verified current metadata and exact catalog fingerprint",
            "confirmation": "REVIEW " + draft["draft_id"][-8:],
            "baseline_sha256": draft["baseline_sha256"],
            "proposal_sha256": draft["proposal_sha256"]}
    db.fail_write = True
    with pytest.raises(DraftUnavailable):
        store.review(**args)
    assert db.writes == 1
    db.fail_write = False
    db.fail_read = True
    with pytest.raises(DraftUnavailable):
        store.review(**args)
    db.fail_read = False
    db.items[draft["draft_id"]]["document"]["S"] = "{}"
    with pytest.raises(DraftUnavailable):
        store.review(**args)
    assert db.writes == 1


def test_review_routes_enforce_mfa_authority_origin_and_fresh_source():
    db, store = make_store()
    source = deepcopy(REGISTRY)
    draft = store.save(**saving_args())
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: (
            VerifiedAdmin(OWNER, frozenset({"owner"}), True)
            if token == "verified-owner-token" else
            VerifiedAdmin(OTHER, frozenset({"owner"}), True)
            if token == "other-owner-token" else
            VerifiedAdmin(OWNER, frozenset({"owner"}), False)
            if token == "no-mfa-token" else None),
        catalog_provider=lambda: source, catalog_drafts=store))
    client = TestClient(app)
    endpoint = "/v1/admin/catalog/drafts/" + draft["draft_id"] + "/review"
    body = {"confirmation": "REVIEW " + draft["draft_id"][-8:],
            "reason": "Independent verified review of metadata proposal",
            "idempotency_key": "review-safe-unique-key-123"}
    owner = {"Authorization": "Bearer verified-owner-token",
             "Origin": "https://guardoath.com"}
    assert client.post(endpoint, json=body).status_code == 401
    assert client.post(endpoint, json=body, headers={"Authorization": "Bearer verified-owner-token"}).status_code == 403
    assert client.post(endpoint, json=body, headers={
        **owner, "Origin": "https://evil.invalid"}).status_code == 403
    assert client.post(endpoint, json=body, headers={
        "Authorization": "Bearer no-mfa-token", "Origin": "https://guardoath.com"}).status_code == 403
    assert client.post(endpoint, json=body, headers={
        "Authorization": "Bearer other-owner-token", "Origin": "https://guardoath.com"}).status_code == 503
    assert client.post(endpoint, json={**body, "confirmation": "REVIEW 12345678"},
                       headers=owner).status_code == 422
    assert db.writes == 1
    response = client.post(endpoint, json=body, headers=owner)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["persisted"] is True and receipt["reviewed"] is True
    assert receipt["published"] is False and receipt["ready_to_publish"] is False
    review_id = receipt["review_id"]
    check = client.get("/v1/admin/catalog/reviews/" + review_id, headers=owner)
    assert check.status_code == 200
    assert check.json()["review"]["review_id"] == review_id
    assert check.json()["review"]["published"] is False
    assert db.writes == 2
    again = client.post(endpoint, json=body, headers=owner)
    assert again.status_code == 200 and again.json()["review_id"] == review_id
    assert db.writes == 2
    assert client.get("/v1/admin/catalog/reviews/" + review_id).status_code == 401
    assert client.get("/v1/admin/catalog/reviews/" + review_id, headers={
        "Authorization": "Bearer other-owner-token"}).status_code == 503
    source["domains"][0]["controls"][0]["rules"][0]["title"] = "Stale current catalog"
    assert client.post(endpoint, json={**body, "idempotency_key": "fresh-review-key-1234"},
                       headers=owner).status_code == 409
    assert db.writes == 2
