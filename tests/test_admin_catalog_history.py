"""Shipped catalog source-history evidence must fail closed across the owner UI/API."""
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guardoath.admin_audit import AdminSensitiveReadAuditor
from guardoath.admin_catalog import read_catalog
from guardoath.admin_catalog_history import CatalogHistoryUnavailable, verified_catalog_history
from guardoath.admin_catalog_preview import _fingerprint
from guardoath.admin_console import VerifiedAdmin, create_admin_router

OWNER = "12345678-1234-1234-1234-123456789012"
SOURCE = {"registry_version": "3", "domains": [
    {"domain_id": "privacy", "name": "Privacy", "status": "active", "controls": [
        {"control_id": "pii", "name": "PII", "stages": ["input", "output"],
         "rules": [{"rule_id": "pii.email", "title": "Original email description",
                    "use_case": "Turn this on to protect email addresses"}]}
    ]}]}
BASE = {"base_registry_version": "3", "releases": []}


def fingerprint(catalog):
    return _fingerprint("guardoath-admin-catalog-source-v1", catalog)


def source_chain(*, rollback=False):
    published = read_catalog(lambda: deepcopy(SOURCE))
    previous = deepcopy(published)
    published["categories"][0]["checks"][0]["rules"][0]["title"] = "Reviewed email description"
    published["registry_version"] = "4"
    first = {
        "registry_version": "4", "category_id": "privacy", "check_id": "pii",
        "rule_id": "pii.email", "review_id": "a" * 64, "draft_id": "b" * 64,
        "baseline_sha256": fingerprint(previous),
        "candidate_sha256": fingerprint(published),
        "changes": {"title": {"before": "Original email description",
                              "after": "Reviewed email description"}},
    }
    history = {"base_registry_version": "3", "releases": [first]}
    if rollback:
        before = deepcopy(published)
        published["registry_version"] = "5"
        published["categories"][0]["checks"][0]["rules"][0]["title"] = "Original email description"
        history["releases"].append({
            "registry_version": "5", "category_id": "privacy", "check_id": "pii",
            "rule_id": "pii.email", "review_id": "c" * 64, "draft_id": "d" * 64,
            "baseline_sha256": fingerprint(before),
            "candidate_sha256": fingerprint(published),
            "changes": {"title": {"before": "Reviewed email description",
                                  "after": "Original email description"}},
        })
    return published, history


def test_baseline_history_is_a_verified_zero_not_an_inferred_provider_zero():
    original = read_catalog(lambda: deepcopy(SOURCE))
    result = verified_catalog_history(original, deepcopy(BASE))
    assert result["status"] == "verified_source_history"
    assert result["release_count"] == 0 and result["releases"] == []
    assert result["source_chain_verified"] is True
    assert result["live_acceptance_verified"] is None


@pytest.mark.parametrize("rollback", [False, True])
def test_complete_source_release_chain_verifies_forward_and_reversal_without_mutation(rollback):
    catalog, history = source_chain(rollback=rollback)
    snapshot, receipts = deepcopy(catalog), deepcopy(history)
    result = verified_catalog_history(catalog, history)
    assert result["release_count"] == (2 if rollback else 1)
    assert result["registry_version"] == ("5" if rollback else "4")
    assert [r["registry_version"] for r in result["releases"]] == (
        ["5", "4"] if rollback else ["4"])
    assert result["releases"][0]["changed_fields"] == ["title"]
    assert "before" not in result["releases"][0]
    assert catalog == snapshot and history == receipts


@pytest.mark.parametrize("mutate", [
    lambda c, h: h["releases"][0].update({"candidate_sha256": "e" * 64}),
    lambda c, h: h["releases"][0].update({"baseline_sha256": "e" * 64}),
    lambda c, h: h["releases"][0].update({"rule_id": "other.rule"}),
    lambda c, h: h["releases"][0]["changes"]["title"].update({"before": "Fake title"}),
    lambda c, h: h["releases"][0]["changes"]["title"].update({"after": "Fake title"}),
    lambda c, h: c["categories"][0]["checks"][0]["rules"][0].update({"title": "Drifted title"}),
    lambda c, h: c.update({"registry_version": "5"}),
    lambda c, h: h["releases"][0].update({"registry_version": "5"}),
    lambda c, h: h["releases"].clear(),
])
def test_malformed_or_stale_source_history_never_fabricates_a_clean_state(mutate):
    catalog, history = source_chain()
    mutate(catalog, history)
    with pytest.raises(CatalogHistoryUnavailable):
        verified_catalog_history(catalog, history)


def test_break_in_middle_of_two_release_chain_is_not_hidden_by_valid_last_version():
    catalog, history = source_chain(rollback=True)
    history["releases"][0]["baseline_sha256"] = "f" * 64
    with pytest.raises(CatalogHistoryUnavailable):
        verified_catalog_history(catalog, history)


class Audit:
    def __init__(self):
        self.writes = []
        self.failed = False

    def put_item(self, **kwargs):
        if self.failed:
            raise RuntimeError("audit unavailable")
        self.writes.append(kwargs["Item"])


def test_owner_catalog_history_api_reuses_protected_existing_read_and_audit():
    source, history = source_chain()
    # The API accepts raw registry_payload, NOT the read_catalog projection.
    raw = deepcopy(SOURCE)
    raw["registry_version"] = source["registry_version"]
    raw["domains"][0]["controls"][0]["rules"][0]["title"] = (
        source["categories"][0]["checks"][0]["rules"][0]["title"])
    assert read_catalog(lambda: deepcopy(raw)) == source
    db = Audit()
    app = FastAPI()
    def verify(token):
        if token == "owner":
            return VerifiedAdmin(OWNER, frozenset({"owner"}), True)
        if token == "no-mfa":
            return VerifiedAdmin(OWNER, frozenset({"owner"}), False)
        return None
    app.include_router(create_admin_router(
        verify_cognito_admin=verify,
        catalog_provider=lambda: deepcopy(raw),
        catalog_history_provider=lambda: deepcopy(history),
        read_auditor=AdminSensitiveReadAuditor(db, "admin-audit", signing_key=b"a" * 32),
        require_persistent_read_audit=True))
    client = TestClient(app)
    path = "/v1/admin/catalog"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-GuardOath-Key": "owner"}).status_code == 401
    assert client.get(path, headers={"X-GuardOath-Admin-Session": "no-mfa"}).status_code == 403
    assert not db.writes
    response = client.get(path, headers={"X-GuardOath-Admin-Session": "owner"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["metadata_releases"]["release_count"] == 1
    assert result["metadata_releases"]["source_chain_verified"] is True
    assert len(result["read_audit_event_id"]) == 64
    assert len(db.writes) == 1  # same pre-existing catalog read audit, no new provider call
    assert db.writes[0]["read_resource"]["S"] == "catalog.registry"
    raw["domains"][0]["controls"][0]["rules"][0]["title"] = "Drift"
    assert client.get(path, headers={"X-GuardOath-Admin-Session": "owner"}).status_code == 503
    db.failed = True
    assert client.get(path, headers={"X-GuardOath-Admin-Session": "owner"}).status_code == 503


def test_unconfigured_history_source_is_explicitly_unavailable_not_zero():
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(OWNER, frozenset({"owner"}), True)
            if token == "owner" else None,
        catalog_provider=lambda: deepcopy(SOURCE)))
    client = TestClient(app)
    result = client.get("/v1/admin/catalog",
        headers={"X-GuardOath-Admin-Session": "owner"})
    assert result.status_code == 200
    assert result.json()["metadata_releases"]["status"] == "unavailable"
    assert result.json()["metadata_releases"]["release_count"] is None
