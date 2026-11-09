"""FC-050 mixed ordinary+advanced regression through one persisted tenant Engine.

The optional fourth return event must not suppress an unrelated ordinary
comparison failure. Both rule findings survive on Input and Output with the
respective tenant-owned actions. All records and data are synthetic.
"""
import json

from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore


TENANT = "fc050-mixed-tenant"
SECRET = "synthetic-fc050-mixed-tenant-secret-for-tests"
TIMES = (
    "2026-11-06T09:00:00Z",
    "2026-11-06T10:00:00Z",
    "2026-11-06T11:00:00Z",
)
FC050 = {
    "rule_id": "mixed_lifecycle",
    "kind": "lifecycle_chronology",
    "fields": {
        "order_at": "/events/0/at",
        "shipment_at": "/events/1/at",
        "delivery_at": "/events/2/at",
        "return_at": "/events/3/at",
    },
    "stages": ["input", "output"],
}
ORDINARY = {
    "rule_id": "mixed_quantity",
    "left": "/summary/observed",
    "right": "/summary/limit",
    "operator": "le",
    "value_type": "number",
    "stages": ["input", "output"],
}
EXPECTED = {"business.mixed_lifecycle", "business.mixed_quantity"}


def _document(*, observed=1, limit=2, return_time=None, include_return=False):
    events = [{"at": value} for value in TIMES]
    if include_return:
        events.append({"at": return_time})
    return {"events": events, "summary": {"observed": observed, "limit": limit}}


def test_fc050_optional_return_preserves_other_rule_findings_in_both_stages():
    token = demo_key(TENANT, SECRET)
    record = TenantRecord(
        tenant_id=TENANT,
        display_name="Synthetic cross-family FC-050 tenant",
        demo=True,
        monthly_allowance=20,
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
    headers = {"Authorization": "Bearer " + token}

    # Persistence assigns revision 1 to a newly created tenant. Use the
    # authoritative saved revision, never the unsaved model's default 0.
    baseline = store.get(TENANT)
    assert baseline.revision >= 1

    saved = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": baseline.revision,
        "changes": {
            "comparisons": [ORDINARY],
            "advanced_conversion_time": [FC050],
            "input_check_cross_field": "block",
            "output_check_cross_field": "flag",
        },
    })
    assert saved.status_code == 200, saved.text[:200]
    persisted = store.get(TENANT)
    assert persisted.revision == baseline.revision + 1
    assert len(persisted.comparisons) == 1
    assert len(persisted.advanced_conversion_time) == 1

    listing = api.get("/v1/applications/current", headers=headers)
    assert listing.status_code == 200
    assert EXPECTED <= {row["rule_id"] for row in listing.json()["tenant"]["record_rules"]}

    cases = (
        # A missing return is normal: no return yet and quantity is in range.
        (_document(), set()),
        # The missing fourth return must not hide an unrelated quantity failure.
        (_document(observed=3), {"business.mixed_quantity"}),
        # Both fields fail: report BOTH findings without early-exit or data echo.
        (_document(observed=3, include_return=True, return_time=TIMES[0]), EXPECTED),
        # An explicitly supplied null return is invalid, not optional.
        (_document(include_return=True, return_time=None), {"business.mixed_lifecycle"}),
    )
    for stage, action in (("input", "block"), ("output", "flag")):
        for document, expected in cases:
            response = api.post("/v1/checks/" + stage, headers=headers, json={
                "application_id": TENANT,
                "content_format": "json",
                "content": json.dumps(document),
            })
            assert response.status_code == 200, response.text[:200]
            result = response.json()
            assert result["evaluation_status"] == "complete"
            business = {
                finding["rule_id"] for finding in result["findings"]
                if finding["rule_id"].startswith("business.")
            }
            assert business == expected, (stage, sorted(business), sorted(expected))
            if expected:
                assert result["effective_action"] == action
            else:
                assert result["effective_action"] == "allow"
            assert all("2026-11-06" not in finding["explanation"]
                       for finding in result["findings"])

    # An invalid candidate update must not change the saved two-family policy.
    stale = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": baseline.revision,
        "changes": {"input_check_cross_field": "flag"},
    })
    assert stale.status_code == 409
    assert store.get(TENANT).comparisons == persisted.comparisons
    assert store.get(TENANT).advanced_conversion_time == persisted.advanced_conversion_time
