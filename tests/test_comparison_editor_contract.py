"""Dashboard CRUD uses the real authenticated tenant settings and Engine."""
import json

from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.models import FieldComparison
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


def test_comparison_editor_create_reload_edit_remove_and_tenant_isolation():
    secret = "comparison-editor-synthetic-secret-0123456789"
    token = demo_key("editor-a", secret)
    keep = FieldComparison(rule_id="keep", left="/existing_left", right="/existing_right", operator="le")
    other_rule = FieldComparison(rule_id="other_owner", left="/private_b_left", right="/private_b_right", operator="le")
    store = MemoryTenantStore((
        TenantRecord(tenant_id="editor-a", display_name="A", demo=True, overage="block",
                     monthly_allowance=100, key_hashes=(key_hash(token),), comparisons=(keep,),
                     input_check_cross_field="block", output_check_cross_field="flag"),
        TenantRecord(tenant_id="editor-b", display_name="B", demo=True, overage="block",
                     monthly_allowance=100, comparisons=(other_rule,), input_check_cross_field="block"),
    ))
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(engine=demo_engine(), credentials={},
                              tenants=TenantService(store, default_checkers(), secret))) as client:
        other_before = store.get("editor-b")
        def current():
            response = client.get("/v1/applications/current", headers=headers)
            assert response.status_code == 200
            assert "/private_b_left" not in response.text
            return response.json()["tenant"]
        def save(changes, revision=None):
            return client.post("/v1/tenants/current/checks", headers=headers, json={
                "revision": current()["revision"] if revision is None else revision, "changes": changes})
        def run(stage, document):
            response = client.post("/v1/checks/" + stage, headers=headers, json={
                "application_id": "editor-a", "content_format": "json",
                "content": json.dumps({"existing_left": 1, "existing_right": 2, **document})})
            assert response.status_code == 200
            return response.json()
        assert current()["comparisons"] == [keep.model_dump(mode="json")]
        new = {"rule_id": "stock", "left": "/opening", "right": "/closing", "operator": "ge",
               "value_type": "number", "stages": ["input", "output"]}
        old_revision = current()["revision"]
        assert save({"comparisons": [keep.model_dump(mode="json"), new],
                     "input_rule_actions": {"business.stock": "block"}}).status_code == 200
        assert current()["comparisons"] == [keep.model_dump(mode="json"), new]
        for stage, expected in (("input", "block"), ("output", "flag")):
            bad = run(stage, {"opening": 10, "closing": 12})
            assert bad["evaluation_status"] == "complete" and bad["effective_action"] == expected
            assert {f["rule_id"] for f in bad["findings"]} == {"business.stock"}
            good = run(stage, {"opening": 10, "closing": 8})
            assert good["effective_action"] == "allow" and good["findings"] == []
        before_stale = store.get("editor-a")
        assert save({"comparisons": [keep.model_dump(mode="json"), new]}, revision=old_revision).status_code == 409
        assert store.get("editor-a") == before_stale
        edited = {**new, "left": "/order", "right": "/ship", "operator": "le",
                  "value_type": "date", "stages": ["output"]}
        assert save({"comparisons": [keep.model_dump(mode="json"), edited],
                     "input_rule_actions": {}, "output_rule_actions": {"business.stock": "block"}}).status_code == 200
        assert current()["comparisons"][1] == edited
        document = {"order": "2026-11-03", "ship": "2026-11-01"}
        assert run("input", document)["findings"] == []
        bad = run("output", document)
        assert bad["effective_action"] == "block"
        assert {f["rule_id"] for f in bad["findings"]} == {"business.stock"}
        assert save({"comparisons": [keep.model_dump(mode="json")], "output_rule_actions": {}}).status_code == 200
        reloaded = current()
        assert reloaded["comparisons"] == [keep.model_dump(mode="json")]
        assert "business.stock" not in {r["rule_id"] for r in reloaded["record_rules"]}
        assert run("output", document)["findings"] == []
        assert store.get("editor-a").used(usage_month()) == 7
        assert store.get("editor-b") == other_before
        assert store.get("editor-b").used(usage_month()) == 0
        assert client.get("/v1/applications/current").status_code == 401


def test_comparison_editor_assets_are_served_under_existing_csp():
    with TestClient(create_app(engine=demo_engine(), credentials={})) as client:
        page = client.get("/dashboard")
        assert page.status_code == 200
        assert '/assets/comparison_editor.js?v=' in page.text
        assert 'id="comparison-editor"' in page.text
        assert "script-src 'self'" in page.headers["content-security-policy"]
        script = client.get("/assets/comparison_editor.js")
        assert script.status_code == 200
        assert "window.comparisonEditor" in script.text
