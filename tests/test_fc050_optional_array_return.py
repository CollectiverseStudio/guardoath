"""FC-050 optional return event: indexed-array semantics in the real tenant Engine.

A fourth event may not exist yet. Absence must not create a false positive, but
an existing invalid/null/out-of-order return and missing mandatory events must
still produce findings on both stages. All documents and tenants are synthetic.
"""
import json

from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.models import AdvancedConversionTimeRule
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore


TENANT = "fc050-optional-array"
SECRET = "fc050-optional-array-secret-for-regression-2026"
RULE = {
    "rule_id": "optional_array_return",
    "kind": "lifecycle_chronology",
    "fields": {
        "order_at": "/events/0/at",
        "shipment_at": "/events/1/at",
        "delivery_at": "/events/2/at",
        "return_at": "/events/3/at",
    },
    "stages": ["input", "output"],
}
ORDER = "2026-11-06T09:00:00Z"
SHIPMENT = "2026-11-06T10:00:00Z"
DELIVERY = "2026-11-06T11:00:00Z"
AFTER = "2026-11-06T12:00:00Z"
BEFORE = "2026-11-06T08:00:00Z"


def _events(*values):
    return {"events": [{"at": value} for value in values]}


def _client():
    token = demo_key(TENANT, SECRET)
    record = TenantRecord(
        tenant_id=TENANT,
        display_name="Synthetic optional array return acceptance",
        demo=True,
        monthly_allowance=30,
        overage="block",
        key_hashes=(key_hash(token),),
        input_check_text_security="flag",
        output_check_text_security="flag",
    )
    store = MemoryTenantStore((record,))
    api = TestClient(create_app(
        engine=demo_engine(), credentials={},
        tenants=TenantService(store, default_checkers(), SECRET),
    ))
    return api, store, {"Authorization": "Bearer " + token}


def _check(api, headers, stage, document):
    response = api.post("/v1/checks/" + stage, headers=headers, json={
        "application_id": TENANT,
        "content_format": "json",
        "content": json.dumps(document),
    })
    assert response.status_code == 200, response.text[:200]
    body = response.json()
    assert body["evaluation_status"] == "complete"
    return body


def test_optional_array_return_is_real_tenant_setting_and_both_stage_engine_contract():
    # Refuse invalid rule shape at the authoritative tenant-model boundary.
    validated = AdvancedConversionTimeRule.model_validate(RULE)
    assert validated.fields["return_at"] == "/events/3/at"

    api, store, headers = _client()
    baseline = store.get(TENANT)
    saved = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": baseline.revision,
        "changes": {
            "advanced_conversion_time": [RULE],
            "input_check_cross_field": "block",
            "output_check_cross_field": "flag",
        },
    })
    assert saved.status_code == 200, saved.text[:250]
    persisted = store.get(TENANT)
    assert persisted.revision == baseline.revision + 1
    assert persisted.advanced_conversion_time == (validated,)
    public = api.get("/v1/applications/current", headers=headers)
    assert public.status_code == 200
    assert public.json()["tenant"]["advanced_conversion_time"][0]["rule_id"] == RULE["rule_id"]
    assert "business.optional_array_return" in {
        row["rule_id"] for row in public.json()["tenant"]["record_rules"]
    }

    clean_before_return = _events(ORDER, SHIPMENT, DELIVERY)
    clean_after_return = _events(ORDER, SHIPMENT, DELIVERY, AFTER)
    invalid_reverse = _events(ORDER, SHIPMENT, DELIVERY, BEFORE)
    invalid_null = _events(ORDER, SHIPMENT, DELIVERY, None)
    missing_mandatory = _events(ORDER, SHIPMENT)
    for stage, action in (("input", "block"), ("output", "flag")):
        for clean in (clean_before_return, clean_after_return):
            result = _check(api, headers, stage, clean)
            assert not any(f["rule_id"] == "business.optional_array_return" for f in result["findings"])
            assert result["effective_action"] == "allow"
        for bad in (invalid_reverse, invalid_null, missing_mandatory):
            result = _check(api, headers, stage, bad)
            assert {f["rule_id"] for f in result["findings"]} == {"business.optional_array_return"}
            assert result["effective_action"] == action
            assert all("2026-11-06" not in f["explanation"] for f in result["findings"])

    # Same tenant revision must reject concurrent stale updates, not erase the rule.
    rejected = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": baseline.revision,
        "changes": {"advanced_conversion_time": []},
    })
    assert rejected.status_code == 409
    assert store.get(TENANT).advanced_conversion_time == (validated,)
