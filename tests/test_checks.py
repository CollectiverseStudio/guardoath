import asyncio

import pytest
from fastapi.testclient import TestClient

from valinator.app import create_app, demo_engine
from valinator.checkers import default_checkers
from valinator.engine import Engine
from valinator.models import Action, CheckerResult, CheckRequest, Policy, Rule, Stage

TOKEN = "test-only-key"


@pytest.fixture
def client():
    return TestClient(create_app(credentials={TOKEN: "demo"}))


def submit(client, stage="input", **overrides):
    body = dict(application_id="demo", policy_version="support-v1", content="Hello",
                payload={"message": "Hello"} if stage == "input" else {"answer": "Hello"})
    body.update(overrides)
    return client.post(f"/v1/checks/{stage}", json=body,
                       headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.mark.parametrize("stage", ["input", "output"])
def test_valid_checks_are_explicitly_simulated(client, stage):
    response = submit(client, stage).json()
    assert response["evaluation_status"] == "complete"
    assert response["effective_action"] == "allow"
    assert response["simulated"] is True
    assert response["cost_basis"] == "local_only"


def test_observe_mode_reports_block_without_enforcing(client):
    response = submit(client, payload={"unexpected": "field"}).json()
    assert response["recommended_action"] == "block"
    assert response["effective_action"] == "allow"
    assert response["findings"][0]["rule_id"] == "schema.invalid"


@pytest.mark.parametrize("stage", ["input", "output"])
def test_stage_selects_correct_schema(client, stage):
    wrong = {"answer": "hello"} if stage == "input" else {"message": "hello"}
    assert submit(client, stage, payload=wrong).json()["recommended_action"] == "block"


@pytest.mark.parametrize("kind,action", [("safety", "block"), ("bias", "flag")])
def test_synthetic_findings_follow_policy(client, kind, action):
    data = submit(client, content=f"[SIMULATE:{kind}:FLAG]").json()
    assert data["recommended_action"] == action
    assert data["findings"][0]["explanation"].startswith("Synthetic")


@pytest.mark.parametrize("failure", ["ERROR", "TIMEOUT"])
def test_required_failures_are_incomplete(client, failure):
    data = submit(client, content=f"[SIMULATE:safety:{failure}]").json()
    assert data["evaluation_status"] == "incomplete"
    assert data["recommended_action"] == "block"
    assert data["effective_action"] == "allow"
    assert data["checker_results"][1]["status"] == "error"


def test_missing_auth_and_bad_auth(client):
    assert client.post("/v1/checks/input", json={}).status_code == 401
    assert client.post("/v1/checks/input", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_unconfigured_service_fails_closed():
    client = TestClient(create_app(credentials={}))
    assert client.get("/healthz").status_code == 200
    assert client.post("/v1/checks/input", json={}).status_code == 503


def test_application_scope_and_unknown_policy(client):
    assert submit(client, application_id="other-tenant").status_code == 403
    assert submit(client, policy_version="unknown").status_code == 404


@pytest.mark.parametrize("changes", [
    {"mode": "enforce"}, {"language": "fr"}, {"content": ""},
    {"content": "x" * 32_001}, {"request_context": "x" * 8_001},
])
def test_invalid_requests_do_not_echo_content(client, changes):
    response = submit(client, **changes)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid check request"}


def run_engine(policy, checkers=None, **overrides):
    request = CheckRequest(application_id="demo", policy_version="v1", content="hello", **overrides)
    return asyncio.run(Engine([policy], checkers or default_checkers()).check(request, Stage.INPUT))


def test_simulators_cannot_enforce_by_default():
    policy = Policy(application_id="demo", version="v1", mode="enforce",
                    rules=(Rule(checker_id="safety"),))
    result = run_engine(policy)
    assert result.effective_action == "block"
    assert result.evaluation_status == "incomplete"
    assert result.checker_results[0].error_code == "simulator_not_enforceable"


def test_deterministic_enforcement():
    policy = Policy(application_id="demo", version="v1", mode="enforce",
                    rules=(Rule(checker_id="schema", on_finding=Action.BLOCK),),
                    input_schema={"type": "integer"})
    assert run_engine(policy, payload="not an integer").effective_action == "block"
    assert run_engine(policy, payload=42).effective_action == "allow"


@pytest.mark.parametrize("required,status", [(True, "incomplete"), (False, "complete")])
def test_missing_adapter_is_visible(required, status):
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="missing", required=required),))
    result = run_engine(policy)
    assert result.evaluation_status == status
    assert result.checker_results[0].status == "unsupported"


def test_required_skipped_check_cannot_pass():
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="schema"),))
    assert run_engine(policy).evaluation_status == "incomplete"


def test_exception_details_do_not_escape():
    async def broken(*args):
        raise RuntimeError("customer-secret-123")
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="broken"),))
    result = run_engine(policy, {"broken": broken})
    assert "customer-secret" not in result.model_dump_json()
    assert result.checker_results[0].error_code == "checker_failure"


def test_malformed_adapter_result_is_a_failure():
    async def broken(*args):
        return CheckerResult(checker_id="broken", checker_version="1", status="flagged")
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="broken"),))
    assert run_engine(policy, {"broken": broken}).evaluation_status == "incomplete"


def test_policy_schema_is_validated_at_startup():
    policy = Policy(application_id="demo", version="v1", rules=(), input_schema={"type": "typo"})
    with pytest.raises(Exception):
        Engine([policy], default_checkers())


def test_duplicate_policy_versions_and_rules_are_rejected():
    policy = Policy(application_id="demo", version="v1", rules=())
    with pytest.raises(ValueError, match="Duplicate policy"):
        Engine([policy, policy], default_checkers())
    policy = policy.model_copy(update={"rules": (Rule(checker_id="schema"), Rule(checker_id="schema"))})
    with pytest.raises(ValueError, match="Duplicate checker"):
        Engine([policy], default_checkers())


def test_responses_and_logs_do_not_retain_source_text(client, caplog):
    secret = "private-customer-content-987"
    response = submit(client, content=secret, payload={"message": secret})
    assert secret not in response.text
    assert secret not in caplog.text


def test_policy_snapshot_cannot_be_changed_by_original_owner():
    schema = {"type": "integer"}
    policy = Policy(application_id="demo", version="v1", mode="enforce",
                    rules=(Rule(checker_id="schema", on_finding=Action.BLOCK),), input_schema=schema)
    engine = Engine([policy], default_checkers())
    schema["type"] = "string"
    request = CheckRequest(application_id="demo", policy_version="v1", content="hello", payload=7)
    assert asyncio.run(engine.check(request, Stage.INPUT)).effective_action == "allow"
