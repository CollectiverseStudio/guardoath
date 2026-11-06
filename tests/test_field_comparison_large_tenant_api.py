"""FC 30/51/100/250/500: real saved tenant, authenticated API, both stages.

No arbitrary comparison-count cap, fixture bypass, or separate validation engine.
Only synthetic numeric JSON is submitted; no outside services or AWS calls.
"""
import json

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


SECRET = "large-field-comparison-tenant-integration-0123456789"
TENANT = "large-fc"


def _client():
    token = demo_key(TENANT, SECRET)
    record = TenantRecord(
        tenant_id=TENANT, display_name="Synthetic FC Scale", mode="enforce",
        demo=True, overage="block", monthly_allowance=20,
        key_hashes=(key_hash(token),),
        input_check_text_security="flag", output_check_text_security="flag",
    )
    store = MemoryTenantStore((record,))
    service = TenantService(store, default_checkers(), SECRET)
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=service))
    return client, store, {"Authorization": "Bearer " + token}


def _comparisons(size):
    return [{"rule_id": f"scale_{i:03d}", "left": f"/left_{i}",
             "right": f"/right_{i}", "operator": "le"}
            for i in range(size)]


def _payload(size, violating=False):
    payload = {key: value for i in range(size)
               for key, value in ((f"left_{i}", 1), (f"right_{i}", 2))}
    if violating:
        payload["left_0"] = 9
        payload[f"left_{size - 1}"] = 9
    return json.dumps(payload)


@pytest.mark.parametrize("size", [30, 51, 100, 250, 500])
def test_authenticated_tenant_field_comparisons_scale_both_stages_and_all_findings(size):
    client, store, headers = _client()
    initial = store.get(TENANT)
    first, last = "business.scale_000", f"business.scale_{size - 1:03d}"
    saved = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": initial.revision,
        "changes": {
            "input_check_cross_field": "flag",
            "output_check_cross_field": "flag",
            "comparisons": _comparisons(size),
            "input_rule_actions": {first: "block", last: "allow"},
            "output_rule_actions": {first: "allow", last: "flag"},
        },
    })
    assert saved.status_code == 200, (size, saved.text[:300])
    current = store.get(TENANT)
    assert current.revision == initial.revision + 1
    assert len(current.comparisons) == size
    assert current.input_rule_actions == {first: "block", last: "allow"}
    assert current.output_rule_actions == {first: "allow", last: "flag"}
    expected = {f"business.scale_{i:03d}" for i in range(size)}
    assert expected <= {r["rule_id"] for r in saved.json()["record_rules"]}

    summary = client.get("/v1/applications/current", headers=headers)
    assert summary.status_code == 200, (size, summary.text[:300])
    assert expected <= {r["rule_id"] for r in summary.json()["tenant"]["record_rules"]}

    for stage, action in (("input", "block"), ("output", "flag")):
        bad = client.post("/v1/checks/" + stage, headers=headers, json={
            "application_id": TENANT, "content_format": "json",
            "content": _payload(size, violating=True),
        })
        assert bad.status_code == 200, (size, stage, bad.text[:300])
        result = bad.json()
        found = {f["rule_id"] for f in result["findings"] if f["rule_id"].startswith("business.scale_")}
        assert found == {first, last}, (size, stage, found)
        assert result["evaluation_status"] == "complete"
        assert result["recommended_action"] == action
        assert result["effective_action"] == action
        assert '"left_0": 9' not in bad.text

        clean = client.post("/v1/checks/" + stage, headers=headers, json={
            "application_id": TENANT, "content_format": "json",
            "content": _payload(size),
        })
        assert clean.status_code == 200, (size, stage, clean.text[:300])
        clean_result = clean.json()
        assert clean_result["evaluation_status"] == "complete"
        assert clean_result["effective_action"] == "allow"
        assert not any(f["rule_id"].startswith("business.scale_") for f in clean_result["findings"])
    assert store.get(TENANT).used(usage_month()) == 4


def test_cross_tenant_access_denied_without_usage_or_rule_leak():
    first, store, headers = _client()
    response = first.post("/v1/checks/input", headers=headers, json={
        "application_id": "another-tenant",
        "content_format": "json",
        "content": '{"left_0": 9, "right_0": 2}',
    })
    assert response.status_code == 403
    assert store.get(TENANT).used(usage_month()) == 0


def test_missing_and_invalid_json_values_are_not_silently_accepted_in_large_tenant():
    size = 51
    client, store, headers = _client()
    initial = store.get(TENANT)
    saved = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": initial.revision,
        "changes": {"input_check_cross_field": "block",
                    "output_check_cross_field": "flag",
                    "comparisons": _comparisons(size)},
    })
    assert saved.status_code == 200, saved.text[:300]
    data = json.loads(_payload(size))
    data.pop("left_1")
    data["right_2"] = "not-a-number"
    for stage, action in (("input", "block"), ("output", "flag")):
        result = client.post("/v1/checks/" + stage, headers=headers, json={
            "application_id": TENANT, "content_format": "json",
            "content": json.dumps(data),
        })
        assert result.status_code == 200, result.text[:300]
        response = result.json()
        found = {f["rule_id"] for f in response["findings"] if f["rule_id"].startswith("business.scale_")}
        assert found == {"business.scale_001", "business.scale_002"}
        assert response["evaluation_status"] == "complete"
        assert response["effective_action"] == action
    assert store.get(TENANT).used(usage_month()) == 2
