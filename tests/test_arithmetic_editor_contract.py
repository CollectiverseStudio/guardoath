"""FC-031–050 arithmetic dashboard read/save contract, real tenant and Engine."""
import json

from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore

SECRET = "arithmetic-dashboard-synthetic-test-secret-2026"


def setup():
    records = []
    headers = {}
    for name in ("arith-alpha", "arith-beta"):
        token = demo_key(name, SECRET)
        headers[name] = {"Authorization": "Bearer " + token}
        records.append(TenantRecord(
            tenant_id=name, display_name=name, demo=True, overage="block",
            monthly_allowance=100, key_hashes=(key_hash(token),),
            input_check_text_security="flag", output_check_text_security="flag"))
    store = MemoryTenantStore(records)
    api = TestClient(create_app(engine=demo_engine(), credentials={},
                                tenants=TenantService(store, default_checkers(), SECRET)))
    return api, store, headers


def rule():
    return {
        "rule_id": "invoice_math",
        "kind": "invoice_line_subtotal",
        "fields": {"lines": "/lines", "subtotal": "/subtotal"},
        "stages": ["input", "output"],
    }


def summary(api, header):
    response = api.get("/v1/applications/current", headers=header)
    assert response.status_code == 200
    return response.json()["tenant"]


def test_arithmetic_editor_tenant_api_save_reload_stage_actions_and_isolation():
    api, store, headers = setup()
    a, b = headers["arith-alpha"], headers["arith-beta"]
    assert api.get("/v1/applications/current").status_code in (401, 403)
    assert summary(api, a)["advanced_arithmetic"] == []
    assert summary(api, b)["advanced_arithmetic"] == []
    initial = store.get("arith-alpha")
    saved = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": initial.revision,
        "changes": {"advanced_arithmetic": [rule()],
                    "input_check_cross_field": "block",
                    "output_check_cross_field": "flag"},
    })
    assert saved.status_code == 200, saved.text[:250]
    assert summary(api, a)["advanced_arithmetic"] == [rule()]
    assert summary(api, b)["advanced_arithmetic"] == []
    assert store.get("arith-beta").advanced_arithmetic == ()

    for stage, expected in (("input", "block"), ("output", "flag")):
        bad = api.post("/v1/checks/" + stage, headers=a, json={
            "content": json.dumps({"lines": [{"quantity": 2, "unit_price": 40},
                                             {"quantity": 1, "unit_price": 30}],
                                   "subtotal": 100}),
            "content_format": "json", "application_id": "arith-alpha",
        })
        assert bad.status_code == 200, bad.text[:250]
        assert "business.invoice_math" in {f["rule_id"] for f in bad.json()["findings"]}
        assert bad.json()["effective_action"] == expected
        clean = api.post("/v1/checks/" + stage, headers=a, json={
            "content": json.dumps({"lines": [{"quantity": 2, "unit_price": 40},
                                             {"quantity": 1, "unit_price": 30}],
                                   "subtotal": 110}),
            "content_format": "json", "application_id": "arith-alpha",
        })
        assert clean.status_code == 200
        assert "business.invoice_math" not in {f["rule_id"] for f in clean.json()["findings"]}

    existing = store.get("arith-alpha")
    changed = dict(rule(), stages=["input"])
    updated = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": existing.revision,
        "changes": {"advanced_arithmetic": [changed],
                    "output_check_cross_field": None},
    })
    assert updated.status_code == 200, updated.text[:250]
    assert summary(api, a)["advanced_arithmetic"][0]["stages"] == ["input"]

    # With Output disabled for this rule and the stage check turned Off,
    # the same invalid arithmetic must not be reported on Output.
    output = api.post("/v1/checks/output", headers=a, json={
        "application_id": "arith-alpha", "content_format": "json",
        "content": json.dumps({"lines": [{"quantity": 2, "unit_price": 40},
                                         {"quantity": 1, "unit_price": 30}],
                               "subtotal": 100}),
    })
    assert output.status_code == 200, output.text[:250]
    assert "business.invoice_math" not in {
        f["rule_id"] for f in output.json()["findings"]
    }

    stale = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": existing.revision,
        "changes": {"advanced_arithmetic": [], "input_check_cross_field": None},
    })
    assert stale.status_code == 409
    assert len(store.get("arith-alpha").advanced_arithmetic) == 1
    revision = store.get("arith-alpha").revision
    removed = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": revision,
        "changes": {"advanced_arithmetic": [], "input_check_cross_field": None},
    })
    assert removed.status_code == 200, removed.text[:250]
    assert summary(api, a)["advanced_arithmetic"] == []
    assert summary(api, b)["advanced_arithmetic"] == []


def test_arithmetic_editor_rejects_invalid_pointer_and_unknown_kind_without_mutation():
    api, store, headers = setup()
    a = headers["arith-alpha"]
    initial = store.get("arith-alpha")
    for candidate in (
        dict(rule(), fields={"lines": "/items/0", "subtotal": "/subtotal"}),
        dict(rule(), kind="unimplemented_model_judgment"),
        dict(rule(), fields={"lines": "/same", "subtotal": "/same/child"}),
    ):
        res = api.post("/v1/tenants/current/checks", headers=a, json={
            "revision": initial.revision,
            "changes": {"advanced_arithmetic": [candidate],
                        "input_check_cross_field": "block"},
        })
        assert res.status_code == 422
        assert store.get("arith-alpha") == initial
