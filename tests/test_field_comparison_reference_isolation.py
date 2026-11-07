"""Authenticated tenant-reference isolation and Batch B collection boundaries."""
import json

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.models import AdvancedIdentityRule
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore

IDS = {"business.scope_guard", "business.lot_guard"}


@pytest.fixture
def tenants():
    secret = "reference-isolation-synthetic-secret-0123456789"
    records, keys = [], {}
    for suffix in ("a", "b"):
        name = "reference-" + suffix
        token = demo_key(name, secret)
        keys[name] = {"Authorization": "Bearer " + token}
        records.append(TenantRecord(
            tenant_id=name, display_name="Synthetic reference " + suffix,
            demo=True, overage="block", monthly_allowance=100,
            key_hashes=(key_hash(token),), input_check_cross_field="block",
            output_check_cross_field="block", advanced_identity=(
                AdvancedIdentityRule(rule_id="scope_guard", kind="authorized_scope_subset",
                                     fields={"requested_scopes": "/scopes"},
                                     trusted_values=("SCOPE_" + suffix.upper(),)),
                AdvancedIdentityRule(rule_id="lot_guard", kind="quarantine_exclusion",
                                     fields={"requested_lot": "/lot"},
                                     trusted_values=("LOT_" + suffix.upper(),)),
            )))
    store = MemoryTenantStore(tuple(records))
    with TestClient(create_app(engine=demo_engine(), credentials={},
                              tenants=TenantService(store, default_checkers(), secret))) as client:
        yield client, store, keys


def check(tenants, owner, stage, data, *, application=None):
    client, _, keys = tenants
    response = client.post("/v1/checks/" + stage, headers=keys[owner], json={
        "application_id": application or owner,
        "content_format": "json", "content": json.dumps(data),
    })
    return response


@pytest.mark.parametrize("stage", ["input", "output"])
def test_identical_rule_ids_keep_distinct_saved_reference_authority(tenants, stage):
    _, store, _ = tenants
    # Each document is clean for exactly one owner. Interleaving also detects
    # accidental cache reuse keyed only by rule ID or kind.
    for _ in range(2):
        for owner, data in (("reference-a", {"lot": "LOT_B", "scopes": ["SCOPE_A"]}),
                            ("reference-b", {"lot": "LOT_A", "scopes": ["SCOPE_B"]})):
            good = check(tenants, owner, stage, data)
            assert good.status_code == 200
            assert good.json()["evaluation_status"] == "complete"
            assert good.json()["findings"] == []
            assert good.json()["effective_action"] == "allow"
            other = "reference-b" if owner == "reference-a" else "reference-a"
            bad = check(tenants, other, stage, data)
            assert bad.status_code == 200
            body = bad.json()
            assert body["evaluation_status"] == "complete"
            assert {f["rule_id"] for f in body["findings"]} == IDS
            assert body["recommended_action"] == body["effective_action"] == "block"
            assert all(value not in bad.text for value in ("LOT_A", "LOT_B", "SCOPE_A", "SCOPE_B"))
    # Eight requests total, four attributed to each authenticated owner.
    assert all(store.get(owner).used(usage_month()) == 4 for owner in ("reference-a", "reference-b"))


@pytest.mark.parametrize("stage", ["input", "output"])
def test_submitted_reference_fields_cannot_replace_saved_authority(tenants, stage):
    response = check(tenants, "reference-a", stage, {
        "lot": "LOT_A", "scopes": ["SCOPE_B"],
        "trusted_values": ["SCOPE_B"], "quarantine": [], "permissions": ["SCOPE_B"],
    })
    assert response.status_code == 200
    assert {f["rule_id"] for f in response.json()["findings"]} == IDS
    assert response.json()["effective_action"] == "block"


@pytest.mark.parametrize("stage", ["input", "output"])
def test_another_application_is_refused_before_metering(tenants, stage):
    response = check(tenants, "reference-a", stage, {"lot": "LOT_A", "scopes": ["SCOPE_B"]},
                     application="reference-b")
    assert response.status_code == 403
    _, store, _ = tenants
    assert all(store.get(owner).used(usage_month()) == 0 for owner in ("reference-a", "reference-b"))


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("action", ["allow", "flag", "off"])
def test_saved_rule_actions_are_stage_and_owner_specific(tenants, stage, action):
    client, store, keys = tenants
    response = client.post("/v1/tenants/current/checks", headers=keys["reference-a"], json={
        "revision": store.get("reference-a").revision,
        "changes": {stage + "_rule_actions": dict.fromkeys(IDS, action)},
    })
    assert response.status_code == 200, response.text[:200]
    data = {"lot": "LOT_A", "scopes": ["SCOPE_B"]}
    result = check(tenants, "reference-a", stage, data).json()
    assert result["evaluation_status"] == "complete"
    assert {f["rule_id"] for f in result["findings"]} == (set() if action == "off" else IDS)
    assert result["effective_action"] == ("allow" if action == "off" else action)
    other_stage = "output" if stage == "input" else "input"
    untouched = check(tenants, "reference-a", other_stage, data).json()
    assert {f["rule_id"] for f in untouched["findings"]} == IDS
    assert untouched["effective_action"] == "block"
    other_owner = check(tenants, "reference-b", stage, {"lot": "LOT_B", "scopes": ["SCOPE_A"]}).json()
    assert {f["rule_id"] for f in other_owner["findings"]} == IDS
    assert other_owner["effective_action"] == "block"


def save_collection_rules(tenants):
    client, store, keys = tenants
    rules = [
        AdvancedIdentityRule(rule_id="scope_guard", kind="authorized_scope_subset",
                             fields={"requested_scopes": "/scopes"}, trusted_values=("SCOPE_A",)),
        AdvancedIdentityRule(rule_id="serial_guard", kind="serial_unique", fields={"serials": "/serials"}),
    ]
    response = client.post("/v1/tenants/current/checks", headers=keys["reference-a"], json={
        "revision": store.get("reference-a").revision,
        "changes": {"advanced_identity": [r.model_dump(mode="json") for r in rules]},
    })
    assert response.status_code == 200, response.text[:200]


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("count", [0, 1, 128, 129])
def test_collection_bounds_apply_even_when_values_otherwise_satisfy_rule(tenants, stage, count):
    save_collection_rules(tenants)
    response = check(tenants, "reference-a", stage, {
        "scopes": ["SCOPE_A"] * count,  # Every entry is authorized, including count 129.
        "serials": ["SER_" + str(i) for i in range(count)],  # All distinct, including count 129.
    })
    assert response.status_code == 200
    body = response.json()
    assert body["evaluation_status"] == "complete"
    valid = count in (1, 128)
    assert {f["rule_id"] for f in body["findings"]} == (
        set() if valid else {"business.scope_guard", "business.serial_guard"})
    assert body["effective_action"] == ("allow" if valid else "block")


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("invalid", [None, True, 1, 1.25, {}, [], "not an identifier"])
def test_invalid_identifier_members_fail_with_all_original_ids(tenants, stage, invalid):
    save_collection_rules(tenants)
    response = check(tenants, "reference-a", stage, {"scopes": [invalid], "serials": [invalid]})
    assert response.status_code == 200
    body = response.json()
    assert body["evaluation_status"] == "complete"
    assert {f["rule_id"] for f in body["findings"]} == {"business.scope_guard", "business.serial_guard"}
    assert body["effective_action"] == "block"
    assert "not an identifier" not in response.text
