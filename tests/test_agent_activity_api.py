"""Authenticated API acceptance for the non-authorizing agent activity diagnostic."""
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import demo_key, demo_records
from guardoath.tenant_store import MemoryTenantStore

SECRET = "agent-activity-test-key-0123456789abcdef"


@pytest.fixture
def client():
    tenants = TenantService(
        MemoryTenantStore(demo_records(key_secret=SECRET)),
        default_checkers(enable_pii=True), SECRET
    )
    with TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants)) as api:
        yield api


def key(tenant="demo1"):
    return {"X-GuardOath-Key": demo_key(tenant, SECRET)}


def action(command="Get-Process"):
    return {"tool": "shell.run", "target": "host-42",
            "arguments": {"command": command}}


def test_requires_tenant_auth_and_exposes_no_untrusted_proof(client):
    body = {"proposed": action(), "observed": action()}
    assert client.post("/v1/agent-activity/compare", json=body).status_code == 401
    response = client.post("/v1/agent-activity/compare", headers=key(), json=body)
    assert response.status_code == 200, response.text
    got = response.json()
    assert got["tenant_id"] == "demo1"
    assert got["status"] == "matching_unverified"
    assert got["verified_execution"] is False
    assert got["authorization"] == "not_granted"
    assert "Get-Process" not in response.text


def test_reports_mismatch_and_no_observation_without_claiming_block(client):
    mismatch = client.post("/v1/agent-activity/compare", headers=key("demo2"), json={
        "proposed": action(), "observed": action("Clear-EventLog -LogName Security")
    })
    assert mismatch.status_code == 200
    assert mismatch.json()["finding_ids"] == ["agent_activity.action_mismatch"]
    assert mismatch.json()["tenant_id"] == "demo2"
    assert "Clear-EventLog" not in mismatch.text
    missing = client.post("/v1/agent-activity/compare", headers=key(), json={
        "proposed": action()
    })
    assert missing.status_code == 200
    assert missing.json()["status"] == "no_observation"
    assert missing.json()["verified_execution"] is False


def test_invalid_claims_fail_without_echo(client):
    bad = client.post("/v1/agent-activity/compare", headers=key(), json={
        "proposed": {**action(), "trusted": True}, "observed": action()
    })
    assert bad.status_code == 422
    assert "Get-Process" not in bad.text


def test_dashboard_exposes_customer_test_flow(client):
    page = client.get("/dashboard").text
    assert 'id="agent-activity-panel"' in page
    assert "This does NOT execute" in page
    assert client.get("/assets/agent_activity.js").status_code == 200
