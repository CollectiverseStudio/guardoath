"""Authenticated comparison-editor read contract: only this tenant's saved basic rules.

Exercises the real existing tenant service, revisioned write API and dashboard
GET surface. No proposed browser code, provider, AWS or customer fixtures.
"""
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore

SECRET = "comparison-summary-isolated-synthetic-0123456789"


def _harness():
    tenants = []
    tokens = {}
    for name in ("edit-alpha", "edit-beta"):
        token = demo_key(name, SECRET)
        tokens[name] = token
        tenants.append(TenantRecord(
            tenant_id=name, display_name=name, demo=True, overage="block",
            monthly_allowance=40, key_hashes=(key_hash(token),),
            input_check_text_security="flag", output_check_text_security="flag",
        ))
    store = MemoryTenantStore(tuple(tenants))
    service = TenantService(store, default_checkers(), SECRET)
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=service))
    headers = {name: {"Authorization": "Bearer " + token}
               for name, token in tokens.items()}
    return client, store, headers


def _summary(client, headers):
    response = client.get("/v1/applications/current", headers=headers)
    assert response.status_code == 200, response.text[:300]
    assert response.headers.get("cache-control") in (None, "no-store")
    return response.json()["tenant"]


def test_comparison_editor_auth_read_and_revisioned_add_update_remove_round_trip():
    client, store, headers = _harness()
    a, b = headers["edit-alpha"], headers["edit-beta"]
    assert _summary(client, a)["comparisons"] == []
    assert _summary(client, b)["comparisons"] == []
    assert client.get("/v1/applications/current").status_code in (401, 403)

    original = store.get("edit-alpha")
    rules = [
        {"rule_id": "amount_cap", "left": "/amount", "right": "/limit",
         "operator": "le", "value_type": "number", "stages": ["input"]},
        {"rule_id": "date_order", "left": "/delivery", "right": "/ordered",
         "operator": "ge", "value_type": "date", "stages": ["output"]},
    ]
    saved = client.post("/v1/tenants/current/checks", headers=a, json={
        "revision": original.revision,
        "changes": {"comparisons": rules, "input_check_cross_field": "block",
                    "output_check_cross_field": "flag"},
    })
    assert saved.status_code == 200, saved.text[:300]
    authoritative = store.get("edit-alpha")
    expected = [item.model_dump(mode="json") for item in authoritative.comparisons]
    assert saved.json()["comparisons"] == expected
    assert _summary(client, a)["comparisons"] == expected
    assert [item["rule_id"] for item in expected] == ["amount_cap", "date_order"]
    assert expected[0]["stages"] == ["input"]
    assert expected[1]["stages"] == ["output"]
    assert _summary(client, b)["comparisons"] == []
    assert store.get("edit-beta").comparisons == ()

    edited = [dict(rules[0], operator="lt"), rules[1]]
    update = client.post("/v1/tenants/current/checks", headers=a, json={
        "revision": authoritative.revision, "changes": {"comparisons": edited}
    })
    assert update.status_code == 200, update.text[:300]
    assert [item["operator"] for item in _summary(client, a)["comparisons"]] == ["lt", "ge"]
    assert update.json()["revision"] == authoritative.revision + 1

    stale = client.post("/v1/tenants/current/checks", headers=a, json={
        "revision": authoritative.revision, "changes": {"comparisons": []}
    })
    assert stale.status_code == 409
    assert len(_summary(client, a)["comparisons"]) == 2

    fresh_revision = store.get("edit-alpha").revision
    removed = client.post("/v1/tenants/current/checks", headers=a, json={
        "revision": fresh_revision,
        "changes": {"comparisons": [],
                    "input_check_cross_field": None, "output_check_cross_field": None},
    })
    assert removed.status_code == 200, removed.text[:300]
    assert removed.json()["comparisons"] == []
    assert _summary(client, a)["comparisons"] == []
    assert _summary(client, b)["comparisons"] == []


def test_comparison_editor_does_not_accept_unsupported_string_type_or_mutate_tenant():
    client, store, headers = _harness()
    original = store.get("edit-alpha")
    response = client.post("/v1/tenants/current/checks", headers=headers["edit-alpha"], json={
        "revision": original.revision,
        "changes": {"input_check_cross_field": "block", "comparisons": [{
            "rule_id": "unsupported_string", "left": "/left", "right": "/right",
            "operator": "eq", "value_type": "string",
        }]},
    })
    assert response.status_code == 422
    assert store.get("edit-alpha") == original
    assert _summary(client, headers["edit-alpha"])["comparisons"] == []
