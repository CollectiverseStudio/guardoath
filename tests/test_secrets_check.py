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
from timing_report import statuses, timed


def github_sample():
    return "ghp" + "_" + "A" * 36


def jwt_sample():
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return encode({"alg":"HS256","typ":"JWT"}) + "." + encode({"sub":"synthetic-demo"}) + "." + encode("not-a-signature")


@pytest.fixture
def engine():
    # Expectation tests check what is found, not timing: they run with the largest allowed timeout (5,000 ms), so a slow
    # CI runner cannot end the scan early (2026-11-04: Python 3.11 CI, a look-alike came back Block because the check
    # did not finish). The 500 ms limit of policies/secrets.json is tested with file_limit_engine below.
    return Engine([policy.model_copy(update={"timeout_ms": 5000}) for policy in load_policies("policies/secrets.json")],
                  default_checkers())


@pytest.fixture
def file_limit_engine():
    """The policies exactly as shipped (timeout_ms 500), for the tests of the time limit itself."""
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
    result, timing = timed(lambda: evaluate(engine, sample))
    # A check that did not finish has no findings either: the message says each checker's status and the timing.
    assert rule in {f.rule_id for f in result.findings}, (statuses(result), timing)
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
    result, timing = timed(lambda: evaluate(engine, sample))
    assert result.findings == ()
    # 2026-11-04, Python 3.11 CI: no findings but Block, which comes from the policy's failure action when the
    # secrets check does not finish. The message now says each checker's status and where the time went.
    assert result.recommended_action == "allow", (statuses(result), timing)


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


def test_timeout_is_incomplete_not_a_pass(file_limit_engine, monkeypatch):
    import guardoath.secrets_check
    values = iter([0, 1])
    monkeypatch.setattr(guardoath.secrets_check, "monotonic", lambda:next(values))
    result = evaluate(file_limit_engine, "hello")
    assert result.evaluation_status == "incomplete"
    assert result.checker_results[0].error_code == "checker_timeout"


def test_scan_deadline_is_the_policy_timeout(file_limit_engine, monkeypatch):
    # policies/secrets.json sets timeout_ms 500. A scan that has used 0.3 s still completes
    # (the old fixed 0.2 s limit failed it); one that has used 0.6 s stops as a timeout.
    import guardoath.secrets_check
    assert load_policies("policies/secrets.json")[0].timeout_ms == 500
    for elapsed, status in [(0.3, "flagged"), (0.6, "error")]:
        calls = iter([0.0])
        monkeypatch.setattr(guardoath.secrets_check, "monotonic", lambda: next(calls, elapsed))
        result = evaluate(file_limit_engine, github_sample())
        assert result.checker_results[0].status == status, elapsed
        if status == "error":
            assert result.evaluation_status == "incomplete"
            assert result.checker_results[0].error_code == "checker_timeout"
        else:
            assert "secrets.github_token" in {f.rule_id for f in result.findings}


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
