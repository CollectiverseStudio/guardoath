"""Identity dashboard: tenant-safe editable definitions, private reference status and real Engine."""
import json
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore

SECRET = "identity-dashboard-test-secret-2026-synthetic-1234"


def setup(protected=False):
    records = []
    headers = {}
    for name in ("identity-alpha", "identity-beta"):
        key = demo_key(name, SECRET)
        headers[name] = {"Authorization": "Bearer " + key}
        rules = ()
        if protected and name == "identity-alpha":
            rules = ({
                "rule_id": "private_quarantine", "kind": "quarantine_exclusion",
                "fields": {"requested_lot": "/lot"},
                "trusted_values": ["SECRET_LOT_001"],
                "stages": ["input", "output"],
            },)
        records.append(TenantRecord(
            tenant_id=name, display_name=name, demo=True, overage="block",
            monthly_allowance=100, key_hashes=(key_hash(key),),
            input_check_text_security="flag", output_check_text_security="flag",
            advanced_identity=rules))
    store = MemoryTenantStore(tuple(records))
    api = TestClient(create_app(engine=demo_engine(), credentials={},
                                tenants=TenantService(store, default_checkers(), SECRET)))
    return api, store, headers


def summary(api, header):
    result = api.get("/v1/applications/current", headers=header)
    assert result.status_code == 200
    return result.json()["tenant"]


RULES = [
    {"rule_id": "order_match", "kind": "order_id_match",
     "fields": {"order_id": "/order_id", "shipment_order_id": "/shipment_order_id"},
     "trusted_values": [], "stages": ["input", "output"]},
    {"rule_id": "unique_serials", "kind": "serial_unique",
     "fields": {"serials": "/serials"},
     "trusted_values": [], "stages": ["input", "output"]},
]


def test_editable_identity_round_trip_and_both_stage_real_engine_findings():
    api, store, headers = setup()
    a, b = headers["identity-alpha"], headers["identity-beta"]
    assert summary(api, a)["advanced_identity"] == []
    assert summary(api, b)["advanced_identity"] == []
    assert api.get("/v1/applications/current").status_code in (401, 403)
    original = store.get("identity-alpha")
    saved = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": original.revision,
        "changes": {"advanced_identity": RULES, "input_check_cross_field": "block",
                    "output_check_cross_field": "flag"},
    })
    assert saved.status_code == 200, saved.text[:350]
    assert summary(api, a)["advanced_identity"] == RULES
    assert summary(api, b)["advanced_identity"] == []
    assert store.get("identity-beta").advanced_identity == ()

    bad = {"order_id": "ORDER1", "shipment_order_id": "ORDER2", "serials": ["X1", "X1"]}
    good = {"order_id": "ORDER1", "shipment_order_id": "ORDER1", "serials": ["X1", "X2"]}
    for stage, action in (("input", "block"), ("output", "flag")):
        response = api.post("/v1/checks/" + stage, headers=a, json={
            "application_id": "identity-alpha", "content_format": "json",
            "content": json.dumps(bad),
        })
        assert response.status_code == 200, response.text[:350]
        body = response.json()
        assert body["evaluation_status"] == "complete"
        assert {"business.order_match", "business.unique_serials"} <= {
            finding["rule_id"] for finding in body["findings"]
        }
        assert body["effective_action"] == action
        clean = api.post("/v1/checks/" + stage, headers=a, json={
            "application_id": "identity-alpha", "content_format": "json",
            "content": json.dumps(good),
        })
        assert clean.status_code == 200
        assert not {f["rule_id"] for f in clean.json()["findings"]} & {
            "business.order_match", "business.unique_serials"
        }

    first = store.get("identity-alpha")
    changed = [dict(RULES[0], stages=["input"]), RULES[1]]
    updated = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": first.revision, "changes": {"advanced_identity": changed},
    })
    assert updated.status_code == 200, updated.text[:350]
    assert summary(api, a)["advanced_identity"][0]["stages"] == ["input"]
    stale = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": first.revision,
        "changes": {"advanced_identity": [], "input_check_cross_field": None,
                    "output_check_cross_field": None},
    })
    assert stale.status_code == 409
    assert len(store.get("identity-alpha").advanced_identity) == 2
    removed = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": store.get("identity-alpha").revision,
        "changes": {"advanced_identity": [],
                    "input_check_cross_field": None, "output_check_cross_field": None},
    })
    assert removed.status_code == 200, removed.text[:350]
    assert summary(api, a)["advanced_identity"] == []
    assert summary(api, b)["advanced_identity"] == []


def test_reference_backed_identity_values_do_not_leak_through_dashboard_summary():
    api, store, headers = setup(protected=True)
    a, b = headers["identity-alpha"], headers["identity-beta"]
    body = summary(api, a)
    assert body["advanced_identity"] == []
    assert body["advanced_identity_reference_status"] == [{
        "rule_id": "private_quarantine", "kind": "quarantine_exclusion",
        "stages": ["input", "output"], "reference_configured": True,
    }]
    assert "SECRET_LOT_001" not in json.dumps(body)
    assert "SECRET_LOT_001" not in api.get("/v1/applications/current", headers=a).text
    assert summary(api, b)["advanced_identity_reference_status"] == []
    assert store.get("identity-alpha").advanced_identity[0].trusted_values == ("SECRET_LOT_001",)

    # An unrelated tenant setting update must preserve the opaque trusted set.
    prior = store.get("identity-alpha")
    changed = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": prior.revision,
        "changes": {"input_check_text_security": "block"},
    })
    assert changed.status_code == 200, changed.text[:300]
    assert store.get("identity-alpha").advanced_identity == prior.advanced_identity
    assert "SECRET_LOT_001" not in changed.text


def test_identity_invalid_pointers_and_reference_value_admission_fail_closed():
    api, store, headers = setup()
    a = headers["identity-alpha"]
    original = store.get("identity-alpha")
    for invalid in (
        dict(RULES[0], fields={"order_id": "/0", "shipment_order_id": "/shipment_order_id"}),
        dict(RULES[0], fields={"order_id": "/same", "shipment_order_id": "/same/child"}),
        dict(RULES[0], trusted_values=["UNAUTHORIZED"]),
        dict(RULES[0], kind="unrecognized_identity_kind"),
        dict(RULES[0], kind=["order_id_match"]),
        dict(RULES[0], kind={"malformed": "kind"}),
    ):
        response = api.post("/v1/tenants/current/checks", headers=a, json={
            "revision": original.revision,
            "changes": {"advanced_identity": [invalid], "input_check_cross_field": "block"},
        })
        assert response.status_code == 422
        assert store.get("identity-alpha") == original


def test_identity_protected_reference_cannot_be_replaced_or_disclosed_by_checks_api():
    api, store, headers = setup(protected=True)
    a = headers["identity-alpha"]
    original = store.get("identity-alpha")
    for supplied in (
        [],
        RULES,
        [{"rule_id": "private_quarantine", "kind": "quarantine_exclusion",
          "fields": {"requested_lot": "/lot"}, "trusted_values": ["REPLACED_LOT"],
          "stages": ["input", "output"]}],
    ):
        response = api.post("/v1/tenants/current/checks", headers=a, json={
            "revision": original.revision, "changes": {"advanced_identity": supplied},
        })
        assert response.status_code == 422, response.text[:300]
        assert "SECRET_LOT_001" not in response.text
        assert store.get("identity-alpha") == original


def test_generic_checks_api_refuses_to_provision_unverified_reference_identity():
    api, store, headers = setup()
    a = headers["identity-alpha"]
    original = store.get("identity-alpha")
    candidate = {
        "rule_id": "client_quarantine", "kind": "quarantine_exclusion",
        "fields": {"requested_lot": "/lot"},
        "trusted_values": ["CLAIMED_TRUSTED_LOT"], "stages": ["input"],
    }
    response = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": original.revision, "changes": {"advanced_identity": [candidate]},
    })
    assert response.status_code == 422
    assert "CLAIMED_TRUSTED_LOT" not in response.text
    assert store.get("identity-alpha") == original


def test_demo_reset_can_clear_private_reference_rules_only_via_explicit_demo_reset():
    api, store, headers = setup(protected=True)
    a = headers["identity-alpha"]
    before = store.get("identity-alpha")
    assert before.demo and before.advanced_identity
    # Ordinary tenant checks API cannot replace/delete a protected reference.
    rejected = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": before.revision, "changes": {"advanced_identity": []},
    })
    assert rejected.status_code == 422
    assert store.get("identity-alpha") == before

    # Authorized demo-only reset is a server-generated full restore; it MUST
    # clear obsolete synthetic private reference arrays for repeatable demos.
    # Every mutation, even an empty reset, must carry JSON media type.\n    reset = api.post("/v1/tenants/current/reset", headers=a, json={})
    assert reset.status_code == 200, reset.text[:250]
    after = store.get("identity-alpha")
    assert after.revision == before.revision + 1
    assert after.advanced_identity == ()
    assert after.input_check_text_security == "block"
    assert "SECRET_LOT_001" not in reset.text
    assert summary(api, a)["advanced_identity_reference_status"] == []
