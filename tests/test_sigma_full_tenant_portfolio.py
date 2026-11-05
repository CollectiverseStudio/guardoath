"""Full tenant Sigma portfolio routing across both trusted Windows event sources.

The authenticated API must evaluate enabled rules of the *selected source*,
not an arbitrary first-128 slice of the full 228-rule tenant configuration.
Uses only shipped server-owned synthetic fixtures, never customer events.
"""
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.sigma_site import catalog_payload
from guardoath.tenant_record import TenantRecord, demo_key, demo_records
from guardoath.tenant_store import MemoryTenantStore


_SECRET = "sigma-full-portfolio-0123456789abcdef"


@pytest.mark.parametrize("category,expected_count", [
    ("ps_script", 125),
    ("process_creation", 103),
])
@pytest.mark.parametrize("mode", ["enforce", "observe"])
def test_full_tenant_sigma_portfolio_routes_only_selected_source_and_keeps_match(
        category, expected_count, mode):
    pinned = catalog_payload()["rules"]
    assert len(pinned) == 228
    matching_source = [row for row in pinned
                       if row["product"] == "windows" and row["category"] == category]
    assert len(matching_source) == expected_count
    selected = matching_source[0]["rule_id"]
    source_ids = {row["rule_id"] for row in matching_source}
    all_ids = {row["rule_id"] for row in pinned}
    assert len(all_ids) == 228

    # Entire tenant inventory is enabled, but only a trusted event source's
    # 125 or 103 matching rules may execute in a single bounded event.
    actions = {identifier: "flag" for identifier in all_ids}
    actions[selected] = "block"
    original = demo_records(key_secret=_SECRET)[0]
    configured = TenantRecord.model_validate({
        **original.model_dump(),
        "mode": mode,
        "sigma_rule_actions": actions,
    })
    tenants = TenantService(
        MemoryTenantStore((configured,)),
        default_checkers(enable_pii=True),
        _SECRET,
    )
    with TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants)) as client:
        response = client.post(
            "/v1/sigma/demo",
            headers={"X-GuardOath-Key": demo_key("demo1", _SECRET)},
            json={"rule_id": selected, "sample": "positive"},
        )
    assert response.status_code == 200, response.text
    got = response.json()
    assert got["evaluation_status"] == "complete"
    assert got["selected_rule_count"] == expected_count
    assert got["sample_rule_matched"] is True
    finding_ids = {finding["rule_id"] for finding in got["findings"]}
    assert selected in finding_ids
    assert finding_ids <= source_ids
    assert got["recommended_action"] == "block"
    assert got["effective_action"] == ("block" if mode == "enforce" else "allow")
    assert got["rule_configuration_source"] == "tenant_record"
    assert got["runtime_registered_as_ai_checker"] is False
