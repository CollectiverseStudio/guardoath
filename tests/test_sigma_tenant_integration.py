"""Tenant-owned Sigma event rule configuration and isolated synthetic API.

Sigma rules are NEVER substituted for AI input/output checkers. The chosen
action comes from the current authenticated tenant record, not request JSON.
"""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.sigma_site import catalog_payload
from guardoath.tenant_record import TenantRecord, demo_key, demo_records, CHECK_IDS
from guardoath.tenant_store import MemoryTenantStore

SECRET = "sigma-site-integration-secret-0123456789abcdef"


@pytest.fixture(scope="module")
def setup():
    records = demo_records(key_secret=SECRET)
    tenants = TenantService(MemoryTenantStore(records), default_checkers(enable_pii=True), SECRET)
    app = create_app(engine=demo_engine(), credentials={}, tenants=tenants)
    with TestClient(app) as client:
        yield client, tenants


def token(tenant="demo1"):
    return {"X-GuardOath-Key": demo_key(tenant, SECRET)}


def sample_id():
    return catalog_payload()["rules"][0]["rule_id"]


def test_catalog_requires_auth_and_lists_every_pinned_rule_without_events(setup):
    client, _ = setup
    assert client.get("/v1/sigma/catalog").status_code == 401
    response = client.get("/v1/sigma/catalog", headers=token())
    assert response.status_code == 200
    data = response.json()
    assert data["domain"] == "security_events"
    assert data["total_rules"] == len(data["rules"]) == 228
    assert data["tenant_enforcement"] is False
    assert all({"rule_id", "title", "use_case", "author", "source_url", "category"} <= row.keys()
               for row in data["rules"])
    assert all("positive_event" not in row and "lookalike_event" not in row for row in data["rules"])
    assert "sigma" not in CHECK_IDS


def test_sigma_actions_are_tenant_fields_with_default_off_and_strict_ids(setup):
    client, tenants = setup
    rule_id = sample_id()
    current = tenants.store.get("demo1")
    assert current.sigma_rule_actions == {}
    assert "sigma_rule_actions" in TenantRecord.model_fields
    with pytest.raises(ValidationError, match="Unknown Sigma event rule id"):
        TenantRecord.model_validate({**current.model_dump(), "sigma_rule_actions": {"not-a-source-rule": "block"}})
    with pytest.raises(ValidationError):
        TenantRecord.model_validate({**current.model_dump(), "sigma_rule_actions": {rule_id: "redact"}})


def test_tenant_action_is_saved_isolated_and_authoritative_for_both_samples(setup):
    client, tenants = setup
    rule_id = sample_id()
    body = {"rule_id": rule_id, "sample": "positive"}
    # Explicitly off by default, even with a valid demo API key.
    assert client.post("/v1/sigma/demo", headers=token(), json=body).status_code == 403
    current = tenants.store.get("demo1")
    changed = client.post("/v1/tenants/current/checks", headers=token(), json={
        "revision": current.revision,
        "changes": {"sigma_rule_actions": {rule_id: "block"}},
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["sigma_rule_actions"] == {rule_id: "block"}
    assert client.post("/v1/sigma/demo", headers=token("demo2"), json=body).status_code == 403
    positive = client.post("/v1/sigma/demo", headers=token(), json=body)
    assert positive.status_code == 200, positive.text
    got = positive.json()
    assert got["matched"] is True and got["mode"] == "synthetic_preview"
    assert got["recommended_action"] == got["effective_action"] == "block"
    assert got["tenant_id"] == "demo1"
    assert got["rule_configuration_source"] == "tenant_record"
    assert got["runtime_registered_as_ai_checker"] is False
    negative = client.post("/v1/sigma/demo", headers=token(), json={**body, "sample": "lookalike"})
    assert negative.status_code == 200, negative.text
    assert negative.json()["matched"] is False
    assert negative.json()["effective_action"] == "allow"
    assert tenants.store.get("demo2").sigma_rule_actions == {}


def test_disabling_sigma_rule_prevents_further_event_demo(setup):
    client, tenants = setup
    current = tenants.store.get("demo1")
    change = client.post("/v1/tenants/current/checks", headers=token(), json={
        "revision": current.revision, "changes": {"sigma_rule_actions": {}},
    })
    assert change.status_code == 200
    assert client.post("/v1/sigma/demo", headers=token(), json={
        "rule_id": sample_id(), "sample": "positive",
    }).status_code == 403


def test_all_pinned_rules_are_configurable_without_an_arbitrary_50_rule_tenant_cap():
    ids = [rule["rule_id"] for rule in catalog_payload()["rules"]]
    assert len(ids) == 228 and len(set(ids)) == 228
    original = demo_records(key_secret=SECRET)[0]
    settings = {identifier: "flag" for identifier in ids}
    candidate = TenantRecord.model_validate({
        **original.model_dump(), "sigma_rule_actions": settings
    })
    assert candidate.sigma_rule_actions == settings
    assert candidate.to_policy().rules  # Existing AI validations remain separate.


def test_observe_mode_preserves_tenant_recommendation_but_allows_synthetic_event():
    rule_id = sample_id()
    original = demo_records(key_secret=SECRET)[0]
    observing = TenantRecord.model_validate({
        **original.model_dump(), "mode": "observe",
        "sigma_rule_actions": {rule_id: "block"},
    })
    tenants = TenantService(MemoryTenantStore((observing,)),
                            default_checkers(enable_pii=True), SECRET)
    with TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants)) as client:
        response = client.post("/v1/sigma/demo", headers=token(), json={
            "rule_id": rule_id, "sample": "positive",
        })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["matched"] is True
    assert data["recommended_action"] == "block"
    assert data["effective_action"] == "allow"


def test_sigma_demo_rejects_arbitrary_event_input_even_when_rule_is_enabled():
    rule_id = sample_id()
    original = demo_records(key_secret=SECRET)[0]
    configured = TenantRecord.model_validate({
        **original.model_dump(), "sigma_rule_actions": {rule_id: "flag"}
    })
    tenants = TenantService(MemoryTenantStore((configured,)),
                            default_checkers(enable_pii=True), SECRET)
    with TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants)) as client:
        response = client.post("/v1/sigma/demo", headers=token(), json={
            "rule_id": rule_id, "sample": "positive",
            "event": {"ScriptBlockText": "untrusted caller-supplied event"},
            "trusted_logsource": {"product": "windows", "category": "ps_script"},
        })
    assert response.status_code == 422
    assert "untrusted caller-supplied event" not in response.text
