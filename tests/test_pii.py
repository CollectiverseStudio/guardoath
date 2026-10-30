import asyncio

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Stage
from network_guard import deny_network

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")


@pytest.fixture(scope="module")
def engine():
    return Engine(load_policies("policies/pii-only.json"), default_checkers(enable_pii=True))


def scan(engine, content, content_format="text"):
    return asyncio.run(engine.check(CheckRequest(application_id="demo", policy_version="pii-v1",
                       content=content, content_format=content_format), Stage.INPUT))


@pytest.mark.parametrize("content,rule", [
    ("Reach me at jane@example.com", "pii.email_address"),
    ("Contact jane+support@example.co.uk", "pii.email_address"),
    ("Card 4111 1111 1111 1111", "pii.credit_card"),
    ("Card 4111-1111-1111-1111", "pii.credit_card"),
])
def test_real_local_findings(engine, content, rule):
    result = scan(engine, content)
    assert rule in {f.rule_id for f in result.findings}
    assert result.recommended_action == "flag"
    assert result.effective_action == "allow"
    assert result.simulated is False
    assert result.evaluation_status == "complete"
    assert content not in result.model_dump_json()


@pytest.mark.parametrize("content", [
    "Hello, I need help with my order.", "Version 3.14.10 costs $19.99.",
    "Use @support to ask a question.", "Invalid checksum 4111 1111 1111 1112",
    "Internal reference user@localhost", "Card instructions without a number",
])
def test_benign_lookalikes(engine, content):
    result = scan(engine, content)
    assert result.findings == ()
    assert result.recommended_action == "allow"


@pytest.mark.parametrize("content", [
    r'{"email":"jane\u0040example.com"}',
    r'{"jane\u0040example.com":"key"}',
    r'["jane\u0040example.com"]',
    r'"jane\u0040example.com"',
])
def test_decoded_json_is_scanned(engine, content):
    assert scan(engine, content, "json").findings[0].rule_id == "pii.email_address"


@pytest.mark.parametrize("content", ['{"card":4111111111111111}', r'{"card":"\u0034111111111111111"}'])
def test_json_card_values(engine, content):
    assert scan(engine, content, "json").findings[0].rule_id == "pii.credit_card"


@pytest.mark.parametrize("content", ["not json", '{"x":NaN}', '{"x":1e400}', '{"x":1,"x":2}'])
def test_invalid_json_does_not_pass_pii(engine, content):
    result = scan(engine, content, "json")
    assert result.evaluation_status == "incomplete"
    assert result.checker_results[0].error_code == "invalid_json_for_pii"


def test_no_network_is_used(monkeypatch):
    deny_network(monkeypatch, "Network access attempted")
    engine = Engine(load_policies("policies/pii-only.json"), default_checkers(enable_pii=True))
    assert scan(engine, "jane@example.com").findings


def test_api_output_and_logs_do_not_echo_sensitive_values(engine, caplog):
    client = TestClient(create_app(engine=engine, credentials={"test-key": "demo"}))
    content = "jane@example.com 4111111111111111"
    response = client.post("/v1/checks/output", headers={"Authorization": "Bearer test-key"}, json={
        "application_id": "demo", "policy_version": "pii-v1", "content": content})
    assert response.status_code == 200
    # The domain inside the address is reported once, as part of the email address.
    assert {f["rule_id"] for f in response.json()["findings"]} == {"pii.credit_card", "pii.email_address"}
    assert "jane@example.com" not in response.text + caplog.text
    assert "4111111111111111" not in response.text + caplog.text


def test_environment_enables_pii_without_a_custom_policy(monkeypatch):
    monkeypatch.setenv("GUARDOATH_ENABLE_PII", "1")
    monkeypatch.delenv("GUARDOATH_POLICY_FILE", raising=False)
    client = TestClient(create_app(credentials={"test-key": "demo"}))
    response = client.post("/v1/checks/input", headers={"Authorization": "Bearer test-key"}, json={
        "application_id": "demo", "policy_version": "support-v1", "content": "jane@example.com"})
    assert response.status_code == 200
    assert "pii.email_address" in {f["rule_id"] for f in response.json()["findings"]}
