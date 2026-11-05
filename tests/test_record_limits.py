"""No comparison count limit on a tenant record (2026-11-05, Greg: "we should not limit the comparisons").

What bounds a record is physical: its stored settings must fit one database item (MAX_RECORD_BYTES), checked when a
change is saved, and one save's request body must fit the API's JSON limit. The per-rule setting maps have no separate
cap either (they may name only catalog rules and the record's own rules). Comparisons and required sections both.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.dynamodb_store import record_json
from guardoath.engine import Engine
from guardoath.hosted import TenantService
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage
from guardoath.rule_catalog import RULES
from guardoath.tenant_record import (MAX_RECORD_BYTES, TENANT_POLICY_VERSION, TenantRecord, demo_key, demo_records,
                                     record_bytes)
from guardoath.tenant_store import MemoryTenantStore

CHECKERS = default_checkers()
SECRET = "-".join(["guardoath", "test", "limits", "demo", "key", "0123456789abcdef"])


def many(count, prefix="limit"):
    return [{"rule_id": f"{prefix}_{i}", "left": f"/left_{i}", "right": f"/right_{i}", "operator": "le"}
            for i in range(count)]


def test_a_record_holds_more_comparisons_than_the_old_caps_with_a_setting_for_each():
    # 1,100 comparisons, each with its own input setting: past the old 50-comparison cap, the old record cap on rule
    # settings (catalog + 100) and the old policy cap (1,000).
    count = max(1100, len(RULES) + 150)
    assert count > 50 and count > len(RULES) + 100 and count > 1000
    record = TenantRecord(tenant_id="acme", display_name="Acme", mode="enforce", input_check_cross_field="flag",
                          output_check_cross_field="flag", comparisons=many(count),
                          input_rule_actions={f"business.limit_{i}": "block" for i in range(count)})
    policy = record.to_policy()
    assert len(policy.comparisons) == count and len(policy.input_rule_actions) == count
    engine = Engine([policy], CHECKERS)
    # An empty document: every comparison's fields are missing, so each one reports (content stays under 32,000 chars).
    request = CheckRequest(application_id="acme", policy_version=TENANT_POLICY_VERSION, content="{}", content_format="json")
    incoming = asyncio.run(engine.check(request, Stage.INPUT))
    outgoing = asyncio.run(engine.check(request, Stage.OUTPUT))
    assert len(incoming.findings) == len(outgoing.findings) == count
    assert incoming.recommended_action == Action.BLOCK      # each comparison's own input setting
    assert outgoing.recommended_action == Action.FLAG       # the check's output action
    assert record_bytes(record) < MAX_RECORD_BYTES


def test_a_policy_file_has_no_comparison_or_section_count_limit():
    sections = [{"rule_id": f"s_{i}", "label": f"Section {i}"} for i in range(80)]
    policy = Policy(application_id="a", version="v", rules=(Rule(checker_id="cross_field"), Rule(checker_id="completeness")),
                    comparisons=many(80), required_sections=sections)
    assert len(policy.comparisons) == len(policy.required_sections) == 80
    Engine([policy], CHECKERS)


def test_record_bytes_is_what_the_database_stores():
    record = TenantRecord(tenant_id="acme", display_name="Acme", input_check_cross_field="flag", comparisons=many(25))
    assert record_bytes(record) == len(record_json(record).encode("utf-8"))


def client_and_service():
    tenants = TenantService(MemoryTenantStore(demo_records(key_secret=SECRET)), default_checkers(enable_pii=True), SECRET)
    return TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants)), tenants


def change(client, changes, revision):
    return client.post("/v1/tenants/current/checks", headers={"Authorization": "Bearer " + demo_key("demo1", SECRET)},
                       json={"revision": revision, "changes": changes})


def test_a_demo_user_saves_three_hundred_comparisons_through_the_api():
    client, tenants = client_and_service()
    revision = tenants.store.get("demo1").revision
    response = change(client, {"comparisons": many(300), "input_check_cross_field": "flag"}, revision)
    assert response.status_code == 200, response.text
    assert len(tenants.store.get("demo1").comparisons) == 300
    rules = [rule for rule in response.json()["record_rules"] if rule["control_id"] == "cross_field"]
    assert len(rules) == 300


def test_a_save_that_would_make_the_record_too_large_is_refused_and_nothing_is_stored():
    client, tenants = client_and_service()
    long_pointer = "/" + "p" * 190
    comparisons = [{"rule_id": f"c{i:04d}" + "x" * 70, "left": f"{long_pointer}{i:04d}/a",
                    "right": f"{long_pointer}{i:04d}/b", "operator": "le"} for i in range(330)]
    first = change(client, {"comparisons": comparisons}, tenants.store.get("demo1").revision)
    assert first.status_code == 200, first.text
    sections = [{"rule_id": f"s{i:04d}" + "y" * 70, "label": f"Section {i:04d}" + "z" * 60, "min_characters": 1}
                for i in range(800)]
    before = tenants.store.get("demo1")
    second = change(client, {"required_sections": sections}, before.revision)
    assert second.status_code == 422
    detail = second.json()["detail"]
    assert f"{MAX_RECORD_BYTES:,} bytes" in detail and "Section 0" not in detail and "yyy" not in detail
    assert tenants.store.get("demo1") == before
