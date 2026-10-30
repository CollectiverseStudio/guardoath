import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Stage
from network_guard import deny_network


def github_sample():
    return "ghp" + "_" + "A" * 36


def jwt_sample():
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return encode({"alg":"HS256","typ":"JWT"}) + "." + encode({"sub":"synthetic-demo"}) + "." + encode("not-a-signature")


@pytest.fixture
def engine():
    return Engine(load_policies("policies/secrets.json"), default_checkers())


def evaluate(engine, content, stage=Stage.INPUT, content_format="text"):
    return asyncio.run(engine.check(CheckRequest(application_id="demo",policy_version="secrets-v1",
                           content=content,content_format=content_format),stage))


@pytest.mark.parametrize("sample,rule", [
    (github_sample(), "secrets.github_token"),
    ("AK" + "IA" + "A" * 16, "secrets.aws_credentials"),
    ('aws_secret_access_key = "' + "a" * 40 + '"', "secrets.aws_credentials"),
    ("sk-" + "A" * 20 + "T3BlbkFJ" + "B" * 20, "secrets.openai_token"),
    ("sk_" + "live_" + "a" * 24, "secrets.stripe_key"),
    (jwt_sample(), "secrets.jwt"),
    ("-----BEGIN" + " RSA PRIVATE KEY-----\nsynthetic-body\n-----END RSA PRIVATE KEY-----", "secrets.private_key"),
    ("-----BEGIN" + " OPENSSH PRIVATE KEY-----", "secrets.private_key"),
])
def test_supported_candidates(engine, sample, rule):
    result = evaluate(engine, sample)
    assert rule in {f.rule_id for f in result.findings}
    assert result.recommended_action == "flag"
    assert result.effective_action == "allow"
    assert result.simulated is False
    assert sample not in result.model_dump_json()
    assert next(g for g in result.groups if g.group_id == "secrets").status == "findings"


@pytest.mark.parametrize("sample", [
    "Please tell me how to rotate an API key.", "password = ${PASSWORD}",
    "api_key = YOUR_API_KEY", "The token is not included here.",
    "jane@example.com", "Order 219099999", "-----BEGIN PUBLIC KEY-----",
    "ghp_short", "sk-not-a-supported-key", "eyJbad.invalid.not-valid-json",
])
def test_benign_and_incomplete_lookalikes(engine, sample):
    result = evaluate(engine, sample)
    assert result.findings == ()
    assert result.recommended_action == "allow"


def test_output_uses_stricter_action(engine):
    result = evaluate(engine, github_sample(), Stage.OUTPUT)
    assert result.recommended_action == "block"
    assert result.effective_action == "allow"  # observe mode


@pytest.mark.parametrize("wrapper", [lambda value:{"token":value}, lambda value:[value], lambda value:{value:"key"}])
def test_decoded_json_keys_and_values_are_scanned(engine, wrapper):
    encoded = json.dumps(wrapper(github_sample())).replace("_", "\\u005f")
    assert evaluate(engine, encoded, content_format="json").findings


@pytest.mark.parametrize("directive", ["# pragma: allowlist secret", "# gitleaks:allow", "# noqa"])
def test_user_allow_comments_cannot_disable_scans(engine, directive):
    assert evaluate(engine, github_sample() + " " + directive).findings


def test_no_verification_or_network(monkeypatch):
    import requests
    from detect_secrets.plugins.aws import AWSKeyDetector
    from detect_secrets.plugins.stripe import StripeDetector
    def deny(*args, **kwargs): raise AssertionError("Verification or network attempted")
    deny_network(monkeypatch, "Verification or network attempted")
    monkeypatch.setattr(requests.Session, "request", deny)
    monkeypatch.setattr(AWSKeyDetector, "verify", deny)
    monkeypatch.setattr(StripeDetector, "verify", deny)
    engine = Engine(load_policies("policies/secrets.json"), default_checkers())
    assert evaluate(engine, "sk_" + "live_" + "a" * 24).findings
    assert evaluate(engine, "AK" + "IA" + "A" * 16).findings


def test_timeout_is_incomplete_not_a_pass(engine, monkeypatch):
    import guardoath.secrets_check
    values = iter([0, 1])
    monkeypatch.setattr(guardoath.secrets_check, "monotonic", lambda:next(values))
    result = evaluate(engine, "hello")
    assert result.evaluation_status == "incomplete"
    assert result.checker_results[0].error_code == "checker_timeout"


def test_invalid_json_is_incomplete(engine):
    result = evaluate(engine, '{"bad":NaN}', content_format="json")
    assert result.evaluation_status == "incomplete"
    assert result.checker_results[0].error_code == "invalid_json_for_secrets"


def test_api_and_logs_never_echo_the_candidate(engine, caplog):
    api = TestClient(create_app(engine=engine,credentials={"test-key":"demo"}))
    response = api.post("/v1/checks/output",headers={"Authorization":"Bearer test-key"},json={
        "application_id":"demo","policy_version":"secrets-v1","content":github_sample()})
    assert response.status_code == 200
    assert github_sample() not in response.text + caplog.text
    assert response.json()["redaction"]["content"] is None
