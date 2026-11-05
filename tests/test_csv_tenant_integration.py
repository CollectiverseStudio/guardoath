"""CSV formula security is a first-class tenant check, not a detached example policy."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.control_registry import registry_payload
from guardoath.engine import Engine
from guardoath.hosted import TenantService
from guardoath.models import CheckRequest, Stage
from guardoath.rule_catalog import RULES
from guardoath.tenant_record import TENANT_POLICY_VERSION, TenantRecord, demo_defaults, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore

SECRET = "csv-tenant-integration-demo-secret-0123456789"
DANGEROUS_CSV = "name,amount\nAlice,=1+2"
CLEAN_CSV = "name,amount\nAlice,25"


def tenant(name, **settings):
    token = demo_key(name, SECRET)
    rec = TenantRecord(tenant_id=name, display_name=name, demo=True, overage="block",
                       monthly_allowance=20, key_hashes=(key_hash(token),),
                       input_check_text_security="flag", **settings)
    return rec, token


def check(engine, rec, stage, content=DANGEROUS_CSV):
    request = CheckRequest(application_id=rec.tenant_id, policy_version=TENANT_POLICY_VERSION,
                           content=content, content_format="text")
    return asyncio.run(engine.check(request, stage))


def test_csv_check_is_in_unified_rule_catalog_and_off_in_demo_defaults():
    assert RULES["csv_formula.potential_formula"].control_id == "csv_formula"
    assert demo_defaults()["input_check_csv_formula"] is None
    assert demo_defaults()["output_check_csv_formula"] is None
    controls = [item for domain in registry_payload(default_checkers())["domains"] for item in domain["controls"]]
    control = next(item for item in controls if item["control_id"] == "csv_formula")
    assert control["field"] == "input_check_csv_formula"
    assert control["output_field"] == "output_check_csv_formula"
    assert control["rule_count"] == 1
    assert control["control_id"] == "csv_formula"
    assert next(domain["domain_id"] for domain in registry_payload(default_checkers())["domains"] if control in domain["controls"]) == "file_security"
    assert control["maturity"]["integrated"] is True
    # No check calls an outside provider; the registry no longer has that field (2026-11-05).
    assert "external_provider" not in control
    assert control["rules"][0]["rule_id"] == "csv_formula.potential_formula"


@pytest.mark.parametrize("input_action,output_action", [
    ("block", "flag"), ("flag", "block"), ("allow", "block"), (None, "block"),
])
def test_per_tenant_stage_settings_are_authoritative(input_action, output_action):
    rec, _ = tenant("demo1", input_check_csv_formula=input_action, output_check_csv_formula=output_action)
    engine = Engine([rec.to_policy()], default_checkers())
    for stage, action in ((Stage.INPUT, input_action), (Stage.OUTPUT, output_action)):
        result = check(engine, rec, stage)
        assert {finding.rule_id for finding in result.findings} == (
            {"csv_formula.potential_formula"} if action else set()
        )
        assert result.effective_action == (action or "allow")
        assert "csv_formula" in [row.checker_id for row in result.checker_results] if action else (
            "csv_formula" not in [row.checker_id for row in result.checker_results]
        )
    assert check(engine, rec, Stage.INPUT, CLEAN_CSV).effective_action == "allow"


def test_per_rule_override_does_not_leak_across_tenants_or_stages():
    a, _ = tenant("demo1", input_check_csv_formula="block", output_check_csv_formula="block",
                  input_rule_actions={"csv_formula.potential_formula": "off"})
    b, _ = tenant("demo2", input_check_csv_formula="flag", output_check_csv_formula="allow")
    engine = Engine([a.to_policy(), b.to_policy()], default_checkers())
    assert not check(engine, a, Stage.INPUT).findings
    assert check(engine, a, Stage.OUTPUT).effective_action == "block"
    assert check(engine, b, Stage.INPUT).effective_action == "flag"
    assert check(engine, b, Stage.OUTPUT).effective_action == "allow"


def test_demo_tenant_api_saves_csv_configuration_and_isolation():
    first, first_token = tenant("demo1")
    second, second_token = tenant("demo2")
    service = TenantService(MemoryTenantStore((first, second)), default_checkers(), SECRET)
    app = create_app(engine=demo_engine(), credentials={}, tenants=service)
    with TestClient(app) as client:
        head = {"Authorization": "Bearer " + first_token}
        other = {"Authorization": "Bearer " + second_token}
        before = client.get("/v1/applications/current", headers=head)
        assert before.status_code == 200
        record = before.json()["tenant"]
        assert record["input_checks"]["csv_formula"] is None
        assert record["output_checks"]["csv_formula"] is None
        response = client.post("/v1/tenants/current/checks", headers=head, json={
            "revision": record["revision"], "changes": {
                "input_check_csv_formula": "flag", "output_check_csv_formula": "block",
            },
        })
        assert response.status_code == 200, response.text
        saved = service.store.get("demo1")
        assert saved.input_check_csv_formula == "flag"
        assert saved.output_check_csv_formula == "block"
        assert service.store.get("demo2").input_check_csv_formula is None
        assert client.post("/v1/tenants/current/checks", headers=head, json={
            "revision": record["revision"], "changes": {"input_check_csv_formula": "block"},
        }).status_code == 409
        for stage, expected in (("input", "flag"), ("output", "block")):
            result = client.post("/v1/checks/" + stage, headers=head, json={
                "application_id": "demo1", "policy_version": "tenant",
                "content": DANGEROUS_CSV, "content_format": "text",
            })
            assert result.status_code == 200, result.text
            assert result.json()["effective_action"] == expected
            assert [f["rule_id"] for f in result.json()["findings"]] == ["csv_formula.potential_formula"]
            assert DANGEROUS_CSV not in result.text
        other_result = client.post("/v1/checks/input", headers=other, json={
            "application_id": "demo2", "policy_version": "tenant",
            "content": DANGEROUS_CSV, "content_format": "text",
        })
        assert other_result.status_code == 200
        assert not any(f["rule_id"].startswith("csv_formula.") for f in other_result.json()["findings"])
