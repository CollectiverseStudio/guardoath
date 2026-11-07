"""Advanced arithmetic customer editor: authoritative typed tenant summary/save and isolation."""
from fastapi.testclient import TestClient
from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.models import ADVANCED_FIELD_KEYS
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore

SECRET = "arithmetic-dashboard-e2e-synthetic-secret-0123456789"


def harness():
    names = ("arith-one", "arith-two")
    records = []
    headers = {}
    for name in names:
        key = demo_key(name, SECRET)
        headers[name] = {"Authorization": "Bearer " + key}
        records.append(TenantRecord(
            tenant_id=name, display_name=name, demo=True, overage="block",
            monthly_allowance=80, key_hashes=(key_hash(key),),
            input_check_text_security="flag", output_check_text_security="flag",
        ))
    store = MemoryTenantStore(tuple(records))
    app = create_app(engine=demo_engine(), credentials={},
                     tenants=TenantService(store, default_checkers(), SECRET))
    return TestClient(app), store, headers


def summary(client, auth):
    result = client.get("/v1/applications/current", headers=auth)
    assert result.status_code == 200, result.text[:250]
    return result.json()["tenant"]


def rules():
    return [
        {"rule_id": "arithmetic_" + str(index), "kind": kind,
         "fields": {role: "/" + kind + "/" + role for role in field_names},
         "stages": ["input", "output"] if index % 2 == 0 else ["input"]}
        for index, (kind, field_names) in enumerate(ADVANCED_FIELD_KEYS.items())
    ]


def test_eight_arithmetic_rule_types_round_trip_through_authenticated_dashboard_summary():
    client, store, headers = harness()
    first, other = headers["arith-one"], headers["arith-two"]
    assert summary(client, first)["advanced_arithmetic"] == []
    assert summary(client, other)["advanced_arithmetic"] == []
    assert client.get("/v1/applications/current").status_code in (401, 403)
    initial = store.get("arith-one")
    definitions = rules()
    created = client.post("/v1/tenants/current/checks", headers=first, json={
        "revision": initial.revision,
        "changes": {"advanced_arithmetic": definitions,
                    "input_check_cross_field": "block",
                    "output_check_cross_field": "flag"},
    })
    assert created.status_code == 200, created.text[:300]
    expected = [rule.model_dump(mode="json") for rule in store.get("arith-one").advanced_arithmetic]
    assert created.json()["advanced_arithmetic"] == expected
    assert summary(client, first)["advanced_arithmetic"] == expected
    assert len(expected) == 8
    assert summary(client, other)["advanced_arithmetic"] == []
    assert store.get("arith-two").advanced_arithmetic == ()

    updated = [dict(expected[0], fields={**expected[0]["fields"],
                  next(iter(expected[0]["fields"])): "/changed/path"}), *expected[1:]]
    changed = client.post("/v1/tenants/current/checks", headers=first, json={
        "revision": store.get("arith-one").revision,
        "changes": {"advanced_arithmetic": updated},
    })
    assert changed.status_code == 200, changed.text[:300]
    assert summary(client, first)["advanced_arithmetic"][0]["fields"] == updated[0]["fields"]
    stale = client.post("/v1/tenants/current/checks", headers=first, json={
        "revision": initial.revision,
        "changes": {"advanced_arithmetic": [],
                    "input_check_cross_field": None, "output_check_cross_field": None},
    })
    assert stale.status_code == 409
    assert len(summary(client, first)["advanced_arithmetic"]) == 8
    removed = client.post("/v1/tenants/current/checks", headers=first, json={
        "revision": store.get("arith-one").revision,
        "changes": {"advanced_arithmetic": [],
                    "input_check_cross_field": None, "output_check_cross_field": None},
    })
    assert removed.status_code == 200, removed.text[:300]
    assert summary(client, first)["advanced_arithmetic"] == []
    assert summary(client, other)["advanced_arithmetic"] == []


def test_arithmetic_invalid_pointer_cannot_mutate_stored_rule_or_tenant():
    client, store, headers = harness()
    auth = headers["arith-one"]
    before = store.get("arith-one")
    bad = rules()[0]
    role = next(iter(bad["fields"]))
    bad["fields"][role] = "/0"
    result = client.post("/v1/tenants/current/checks", headers=auth, json={
        "revision": before.revision,
        "changes": {"advanced_arithmetic": [bad], "input_check_cross_field": "block"},
    })
    assert result.status_code == 422
    assert store.get("arith-one") == before
    assert summary(client, auth)["advanced_arithmetic"] == []
