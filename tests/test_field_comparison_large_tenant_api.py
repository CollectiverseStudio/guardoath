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

    # A stale revision cannot overwrite this 30/51/100/250/500-rule
    # portfolio, even when the stale request carries an otherwise valid
    # unrelated tenant setting. The entire saved authoritative record stays.
    stale = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": initial.revision,
        "changes": {"input_check_text_security": "block"},
    })
    assert stale.status_code == 409, (size, stale.text[:250])
    assert store.get(TENANT) == current

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


def test_501_nested_comparisons_run_without_flat_json_object_limit():
    """501 ordinary rules are valid when fields are distributed into bounded objects.

    The corresponding flat layout references 1,002 root keys and would exceed
    the shared parser's per-object limit of 1,000. Neither the server nor the
    editor should impose an arbitrary 500-comparison ceiling.
    """
    size = 501
    client, store, headers = _client()
    baseline = store.get(TENANT)
    comparisons = [
        {"rule_id": f"nested_{i:03d}",
         "left": f"/left/field_{i:03d}", "right": f"/right/field_{i:03d}",
         "operator": "le", "stages": ["input", "output"]}
        for i in range(size)
    ]
    saved = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": baseline.revision,
        "changes": {"comparisons": comparisons,
                    "input_check_cross_field": "block",
                    "output_check_cross_field": "flag"}
    })
    assert saved.status_code == 200, saved.text[:300]
    assert len(store.get(TENANT).comparisons) == 501
    assert len({r["rule_id"] for r in saved.json()["record_rules"]
                if r["rule_id"].startswith("business.nested_")}) == 501

    payload = {
        "left": {f"field_{i:03d}": 1 for i in range(size)},
        "right": {f"field_{i:03d}": 2 for i in range(size)},
    }
    for stage, action in (("input", "block"), ("output", "flag")):
        clean = client.post("/v1/checks/" + stage, headers=headers, json={
            "application_id": TENANT, "content_format": "json",
            "content": json.dumps(payload),
        })
        assert clean.status_code == 200, clean.text[:300]
        assert clean.json()["evaluation_status"] == "complete"
        assert clean.json()["effective_action"] == "allow"

        payload["left"]["field_000"] = 9
        payload["left"]["field_500"] = 9
        flagged = client.post("/v1/checks/" + stage, headers=headers, json={
            "application_id": TENANT, "content_format": "json",
            "content": json.dumps(payload),
        })
        assert flagged.status_code == 200, flagged.text[:300]
        result = flagged.json()
        assert result["evaluation_status"] == "complete"
        assert result["effective_action"] == action
        found = {f["rule_id"] for f in result["findings"]
                 if f["rule_id"].startswith("business.nested_")}
        assert found == {"business.nested_000", "business.nested_500"}
        assert '"field_000": 9' not in flagged.text
        payload["left"]["field_000"] = 1
        payload["left"]["field_500"] = 1
    assert store.get(TENANT).used(usage_month()) == 4


def test_comparison_32_level_json_limit_matches_customer_warning_and_33_level_is_not_clean():
    """The actual shared Engine rejects overdeep JSON rather than silently passing."""
    client, store, headers = _client()
    current = store.get(TENANT)
    saved = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": current.revision,
        "changes": {
            "comparisons": [{
                "rule_id": "depth_boundary", "left": "/k" * 32,
                "right": "/reference", "operator": "le",
                "stages": ["input", "output"]
            }],
            "input_check_cross_field": "block",
            "output_check_cross_field": "flag",
        }
    })
    assert saved.status_code == 200, saved.text[:250]

    def document(depth):
        data = 2
        for _ in range(depth):
            data = {"k": data}
        data["reference"] = 3
        return json.dumps(data)

    for stage in ("input", "output"):
        acceptable = client.post("/v1/checks/" + stage, headers=headers, json={
            "application_id": TENANT, "content_format": "json",
            "content": document(32)
        })
        assert acceptable.status_code == 200, acceptable.text[:250]
        assert acceptable.json()["evaluation_status"] == "complete"
        assert acceptable.json()["effective_action"] == "allow"

        too_deep = client.post("/v1/checks/" + stage, headers=headers, json={
            "application_id": TENANT, "content_format": "json",
            "content": document(33)
        })
        assert too_deep.status_code == 200, too_deep.text[:250]
        result = too_deep.json()
        assert "business.invalid_json" in {f["rule_id"] for f in result["findings"]}
        # The enabled required text_security checker also rejects malformed JSON:
        # failure_action=block takes precedence even when Output cross_field=flag.
        assert result["evaluation_status"] == "incomplete"
        assert result["recommended_action"] == result["effective_action"] == "block"
        inspection = next(r for r in result["checker_results"]
                          if r["checker_id"] == "text_security")
        assert inspection["status"] == "error"
        assert inspection["error_code"] == "invalid_json_for_text_inspection"
        assert '"k":' not in too_deep.text
    assert store.get(TENANT).used(usage_month()) == 4
