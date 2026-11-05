"""End-to-end synthetic Sigma multi-hit contracts on the tenant API.

The real pinned matchers are evaluated against an isolated synthetic event
that matches two distinct upstream rules. No customer event ingestion is added.
"""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
import guardoath.sigma_site as sigma_site
from guardoath.tenant_record import TenantRecord, demo_key, demo_records
from guardoath.tenant_store import MemoryTenantStore

SECRET = "sigma-multihit-fixture-secret-0123456789abcdef"
FIRST = "846c7a87-8e14-4569-9d49-ecfd4276a01c"
SECOND = "060c3ef1-fd0a-4091-bf46-e7d625f60b73"
SYNTHETIC_EVENT = {"ScriptBlockText": "Get-ADReplAccount -All -Server lab "}
LOOKALIKE_EVENT = {"ScriptBlockText": "Write-Output routine-safe-test"}
SOURCE = {"product": "windows", "category": "ps_script"}


@pytest.fixture
def paired_synthetic_fixture(monkeypatch):
    """Change only this test's selected fixture, never a pinned rule definition."""
    catalog = sigma_site._catalog()
    assert {FIRST, SECOND} <= set(catalog)
    table = sigma_site._routing()
    for identifier in (FIRST, SECOND):
        assert {key: table.source_for(identifier)[key] for key in SOURCE} == SOURCE
    # Existing routing evidence confirms two *real pinned* rules overlap here.
    matched = table.evaluate(SYNTHETIC_EVENT, trusted_logsource=SOURCE,
                             allowed_rule_ids=(FIRST, SECOND))
    assert {row["rule_id"] for row in matched} == {FIRST, SECOND}
    assert table.evaluate(LOOKALIKE_EVENT, trusted_logsource=SOURCE,
                          allowed_rule_ids=(FIRST, SECOND)) == []
    isolated = dict(catalog)
    isolated[FIRST] = replace(
        isolated[FIRST], positive_event=tuple(SYNTHETIC_EVENT.items()),
        lookalike_event=tuple(LOOKALIKE_EVENT.items()),
    )
    monkeypatch.setattr(sigma_site, "_catalog", lambda: isolated)
    return FIRST, SECOND


def _client_with_actions(actions, *, mode="enforce"):
    original = demo_records(key_secret=SECRET)[0]
    configured = TenantRecord.model_validate({
        **original.model_dump(), "mode": mode, "sigma_rule_actions": actions,
    })
    tenants = TenantService(MemoryTenantStore((configured,)),
                            default_checkers(enable_pii=True), SECRET)
    return TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants))


@pytest.mark.parametrize("actions,mode,recommended,effective", [
    ({FIRST: "flag", SECOND: "block"}, "enforce", "block", "block"),
    ({FIRST: "block", SECOND: "allow"}, "enforce", "block", "block"),
    ({FIRST: "allow", SECOND: "flag"}, "enforce", "flag", "flag"),
    ({FIRST: "flag", SECOND: "block"}, "observe", "block", "allow"),
])
def test_two_actual_pinned_rule_hits_preserve_both_findings_and_tenant_action(
        paired_synthetic_fixture, actions, mode, recommended, effective):
    with _client_with_actions(actions, mode=mode) as client:
        positive = client.post("/v1/sigma/demo",
                               headers={"X-GuardOath-Key": demo_key("demo1", SECRET)},
                               json={"rule_id": FIRST, "sample": "positive"})
        assert positive.status_code == 200, positive.text
        result = positive.json()
        assert result["matched"] is True
        assert result["sample_rule_matched"] is True
        assert result["selected_rule_count"] == 2
        assert {row["rule_id"] for row in result["findings"]} == {FIRST, SECOND}
        assert result["recommended_action"] == recommended
        assert result["effective_action"] == effective
        assert result["rule_configuration_source"] == "tenant_record"
        assert result["runtime_registered_as_ai_checker"] is False
        assert SYNTHETIC_EVENT["ScriptBlockText"] not in positive.text
        benign = client.post("/v1/sigma/demo",
                             headers={"X-GuardOath-Key": demo_key("demo1", SECRET)},
                             json={"rule_id": FIRST, "sample": "lookalike"})
        assert benign.status_code == 200, benign.text
        clean = benign.json()
        assert clean["matched"] is False
        assert clean["findings"] == []
        assert clean["recommended_action"] == clean["effective_action"] == "allow"


def test_disabled_companion_cannot_leak_a_match_or_its_stronger_action(paired_synthetic_fixture):
    # Although the actual event also matches SECOND, that source rule is Off.
    with _client_with_actions({FIRST: "flag"}) as client:
        response = client.post("/v1/sigma/demo",
                               headers={"X-GuardOath-Key": demo_key("demo1", SECRET)},
                               json={"rule_id": FIRST, "sample": "positive"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["selected_rule_count"] == 1
        assert [row["rule_id"] for row in result["findings"]] == [FIRST]
        assert result["recommended_action"] == result["effective_action"] == "flag"
        refused = client.post("/v1/sigma/demo",
                              headers={"X-GuardOath-Key": demo_key("demo1", SECRET)},
                              json={"rule_id": SECOND, "sample": "positive"})
        assert refused.status_code == 403
