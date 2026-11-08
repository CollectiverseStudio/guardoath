"""FC-001–030: stage parity across direct model, tenant API, and Engine.

The browser bulk editor already disallows repeated Input/Output stages. Direct
API callers must not bypass that check or silently rewrite the tenant record.
All values are synthetic and no provider or network data is used.
"""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.models import FieldComparison
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore


SECRET = "synthetic-field-comparison-stage-contract-0123456789"
TENANT = "fc-stage-contract"


def _definition(stages):
    return {
        "rule_id": "stage_only",
        "left": "/amount",
        "right": "/limit",
        "operator": "le",
        "value_type": "number",
        "stages": stages,
    }


def _client():
    token = demo_key(TENANT, SECRET)
    record = TenantRecord(
        tenant_id=TENANT, display_name="Synthetic FC stage contract",
        mode="enforce", demo=True, monthly_allowance=20, overage="block",
        key_hashes=(key_hash(token),),
        input_check_text_security="flag", output_check_text_security="flag",
    )
    store = MemoryTenantStore((record,))
    service = TenantService(store, default_checkers(), SECRET)
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=service))
    return client, store, {"Authorization": "Bearer " + token}


@pytest.mark.parametrize("stages", [
    ["input", "input"], ["output", "output"],
])
def test_duplicate_stage_names_are_rejected_by_authoritative_model(stages):
    with pytest.raises(ValidationError, match="Comparison stages must be distinct"):
        FieldComparison.model_validate(_definition(stages))


@pytest.mark.parametrize("stages", [
    ["input", "input"], ["output", "output"], ["input", "input", "output"],
])
def test_direct_tenant_api_rejects_repeated_stages_with_no_revision_or_policy_mutation(stages):
    client, store, headers = _client()
    before = store.get(TENANT)
    reply = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": before.revision,
        "changes": {
            "comparisons": [_definition(stages)],
            "input_check_cross_field": "flag",
            "output_check_cross_field": "block",
        },
    })
    assert reply.status_code == 422, reply.text[:200]
    assert store.get(TENANT) == before
    assert "/amount" not in reply.text and "/limit" not in reply.text


@pytest.mark.parametrize("stage", ["input", "output"])
def test_one_valid_stage_saves_and_runs_through_shared_engine_only_on_that_stage(stage):
    client, store, headers = _client()
    before = store.get(TENANT)
    saved = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": before.revision,
        "changes": {
            "comparisons": [_definition([stage])],
            "input_check_cross_field": "flag",
            "output_check_cross_field": "block",
        },
    })
    assert saved.status_code == 200, saved.text[:200]
    assert store.get(TENANT).revision == before.revision + 1
    assert store.get(TENANT).comparisons[0].stages[0].value == stage

    for actual_stage in ("input", "output"):
        answer = client.post("/v1/checks/" + actual_stage, headers=headers, json={
            "application_id": TENANT, "content_format": "json",
            "content": '{"amount":9,"limit":2}',
        })
        assert answer.status_code == 200, answer.text[:200]
        result = answer.json()
        found = {finding["rule_id"] for finding in result["findings"]}
        if actual_stage == stage:
            assert "business.stage_only" in found
            assert result["effective_action"] == ("flag" if stage == "input" else "block")
        else:
            assert "business.stage_only" not in found
        assert '"amount":9' not in answer.text
