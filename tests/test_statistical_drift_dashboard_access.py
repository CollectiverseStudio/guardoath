"""Owner-only PSI configuration for the actual dashboard, without evaluation-key leakage."""
import json

from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore

SECRET = "synthetic-psi-dashboard-owner-secret"


def _rule():
    return {
        "rule_id": "income_drift", "observations_pointer": "/income_samples",
        "reference_id": "private-tenant-reference", "reference_version": "v1",
        "bin_edges": ["0", "10", "20", "30"], "counts": [10, 10, 10],
        "psi_threshold": "0.15", "min_observations": 30,
        "stages": ["input", "output"],
    }


def _app():
    def record(tenant, demo=True):
        token = demo_key(tenant, SECRET)
        return TenantRecord(
            tenant_id=tenant, display_name=tenant, demo=demo,
            monthly_allowance=12, overage="block", key_hashes=(key_hash(token),),
            input_check_text_security="flag", output_check_text_security="flag",
        )
    store = MemoryTenantStore([record("drift-owner"), record("drift-neighbor"), record("drift-customer", demo=False)])
    api = TestClient(create_app(engine=demo_engine(), credentials={},
                                tenants=TenantService(store, default_checkers(), SECRET)))
    return api, store


def _headers(tenant):
    return {"Authorization": "Bearer " + demo_key(tenant, SECRET)}


def test_owner_can_reload_full_reference_without_public_summary_leakage():
    client, store = _app()
    owner = _headers("drift-owner")
    initial = client.get("/v1/tenants/current/statistical-drift", headers=owner)
    assert initial.status_code == 200
    assert initial.headers["cache-control"] == "no-store"
    assert initial.json() == {"revision": store.get("drift-owner").revision, "rules": []}
    rule = _rule()
    saved = client.post("/v1/tenants/current/checks", headers=owner, json={
        "revision": initial.json()["revision"],
        "changes": {"advanced_statistical_drift": [rule], "input_check_cross_field": "block"},
    })
    assert saved.status_code == 200, saved.text[:250]
    assert "advanced_statistical_drift" not in saved.json()
    current = client.get("/v1/applications/current", headers=owner)
    assert current.status_code == 200
    assert "advanced_statistical_drift" not in current.text
    assert "private-tenant-reference" not in current.text
    reloaded = client.get("/v1/tenants/current/statistical-drift", headers=owner)
    assert reloaded.status_code == 200
    assert reloaded.json()["rules"] == [rule]
    assert reloaded.json()["revision"] == store.get("drift-owner").revision
    # A different tenant cannot read this tenant's saved histogram.
    neighbor = client.get("/v1/tenants/current/statistical-drift", headers=_headers("drift-neighbor"))
    assert neighbor.status_code == 200
    assert neighbor.json()["rules"] == []
    assert "private-tenant-reference" not in neighbor.text
    # An evaluation-only customer key may run checks, but cannot read the
    # owner-only reference dataset or edit it without an authenticated session.
    customer = client.get("/v1/tenants/current/statistical-drift", headers=_headers("drift-customer"))
    assert customer.status_code == 403
    assert "private-tenant-reference" not in customer.text


def test_owner_revision_conflict_and_invalid_drift_do_not_change_reference():
    client, store = _app()
    headers = _headers("drift-owner")
    before = store.get("drift-owner").revision
    rule = _rule()
    valid = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": before,
        "changes": {"advanced_statistical_drift": [rule], "input_check_cross_field": "block"},
    })
    assert valid.status_code == 200
    stale = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": before, "changes": {
            "advanced_statistical_drift": [], "input_check_cross_field": None,
        },
    })
    assert stale.status_code == 409
    assert client.get("/v1/tenants/current/statistical-drift", headers=headers).json()["rules"] == [rule]
    bad = {**rule, "counts": [10, True, 19]}
    rejected = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": store.get("drift-owner").revision,
        "changes": {"advanced_statistical_drift": [bad]},
    })
    assert rejected.status_code == 422
    assert client.get("/v1/tenants/current/statistical-drift", headers=headers).json()["rules"] == [rule]
    # No app-level error echoes the source histogram or original JSON body.
    assert "private-tenant-reference" not in rejected.text
