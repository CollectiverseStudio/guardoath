import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckerResult, CheckRequest, Policy, Rule, Stage

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")


@pytest.fixture(scope="module")
def checkers():
    return default_checkers(enable_pii=True)


def evaluate(checkers, content, *, mode="observe", rules=None, content_format="text", schema=None, stage=Stage.INPUT):
    policy = Policy(application_id="demo", version="v1", mode=mode, timeout_ms=500,
                    rules=rules or (Rule(checker_id="pii", on_finding=Action.REDACT),),
                    input_schema=schema, output_schema=schema)
    request = CheckRequest(application_id="demo", policy_version="v1", content=content, content_format=content_format)
    return asyncio.run(Engine([policy], checkers).check(request, stage))


@pytest.mark.parametrize("content", ["SSN: 219-09-9999", "SSN: 219099999", "Social security number: 219 09 9999"])
def test_ssn_candidates_are_detected(checkers, content):
    result = evaluate(checkers, content)
    assert "pii.us_ssn" in {f.rule_id for f in result.findings}
    assert "219" not in result.redaction.content
    assert result.redaction.status == "preview"


@pytest.mark.parametrize("content", ["Order 219099999", "SSN: 000-12-3456", "SSN: 666-12-3456", "SSN: 900-12-3456",
                                      "SSN: 219-00-9999", "SSN: 219-09-0000", "SSN: 123-45-6789", "SSN: 219-09.9999"])
def test_ssn_invalid_values_and_lookalikes_are_not_flagged(checkers, content):
    assert evaluate(checkers, content).findings == ()


def test_json_ssn_uses_field_context(checkers):
    result = evaluate(checkers, '{"social_security_number":"219099999"}', content_format="json")
    assert result.redaction.content == '{"social_security_number":"[REDACTED_US_SSN]"}'


def test_all_supported_entities_are_replaced(checkers):
    source = "jane@example.com; SSN 219-09-9999; card 4111111111111111"
    result = evaluate(checkers, source)
    assert result.recommended_action == "redact"
    assert result.effective_action == "allow"
    assert all(c.status == "passed" for c in result.redaction.validation_results)
    serialized = result.model_dump_json()
    for value in ["jane@example.com", "219-09-9999", "4111111111111111"]:
        assert value not in serialized
    assert "transformed_content" not in serialized


def test_enforce_mode_only_returns_rechecked_content(checkers):
    result = evaluate(checkers, "Email jane@example.com", mode="enforce")
    assert result.effective_action == "redact"
    assert result.redaction.status == "ready"
    assert result.redaction.content == "Email [REDACTED_EMAIL_ADDRESS]"


def test_detection_only_does_not_echo_content(checkers):
    result = evaluate(checkers, "Email jane@example.com", rules=(Rule(checker_id="pii"),))
    assert result.redaction.status == "not_requested"
    assert result.redaction.content is None
    assert "jane@example.com" not in result.model_dump_json()


def test_no_findings_returns_no_copy(checkers):
    result = evaluate(checkers, "Private text without a supported identifier")
    assert result.redaction.status == "not_needed"
    assert result.redaction.content is None


def test_json_values_remain_parseable(checkers):
    result = evaluate(checkers, '{"email":"jane\\u0040example.com","amount":15,"list":["219-09-9999"]}', content_format="json")
    value = json.loads(result.redaction.content)
    assert value == {"email":"[REDACTED_EMAIL_ADDRESS]", "amount":15, "list":["[REDACTED_US_SSN]"]}


def test_sensitive_json_key_withholds_content(checkers):
    result = evaluate(checkers, '{"jane@example.com":"hello"}', content_format="json")
    assert result.redaction.status == "withheld"
    assert result.redaction.content is None
    assert result.recommended_action == "block"
    assert "jane@example.com" not in result.model_dump_json()


@pytest.mark.parametrize("source,schema", [
    ('{"email":"jane@example.com"}', {"type":"object","properties":{"email":{"type":"string","format":"email"}}}),
    ('{"card":4111111111111111}', {"type":"object","properties":{"card":{"type":"integer"}}}),
])
def test_transform_that_breaks_schema_is_withheld(checkers, source, schema):
    rules = (Rule(checker_id="schema", on_finding=Action.BLOCK), Rule(checker_id="pii", on_finding=Action.REDACT))
    result = evaluate(checkers, source, content_format="json", schema=schema, rules=rules, mode="enforce")
    assert result.redaction.status == "withheld"
    assert result.effective_action == "block"
    assert result.redaction.content is None
    assert any(c.status == "flagged" for c in result.redaction.validation_results)


def test_simulated_revalidation_cannot_release_content(checkers):
    rules = (Rule(checker_id="pii", on_finding=Action.REDACT), Rule(checker_id="safety"))
    result = evaluate(checkers, "Email jane@example.com", rules=rules)
    assert result.redaction.status == "withheld"
    assert result.redaction.content is None


def test_stage_specific_redaction(checkers):
    rules = (Rule(checker_id="pii", on_finding=Action.FLAG, on_output_finding=Action.REDACT),)
    assert evaluate(checkers, "jane@example.com", rules=rules).redaction.status == "not_requested"
    assert evaluate(checkers, "jane@example.com", rules=rules, stage=Stage.OUTPUT).redaction.status == "preview"


def test_api_key_and_content_are_not_logged(checkers, caplog):
    policy = Policy(application_id="demo", version="v1", timeout_ms=500, rules=(Rule(checker_id="pii",on_finding=Action.REDACT),))
    api = TestClient(create_app(engine=Engine([policy],checkers),credentials={"test-key":"demo"}))
    response = api.post("/v1/checks/output",headers={"Authorization":"Bearer test-key"},json={
        "application_id":"demo","policy_version":"v1","content":"Email jane@example.com; SSN 219-09-9999"})
    assert response.status_code == 200
    assert response.json()["redaction"]["status"] == "preview"
    assert "jane@example.com" not in response.text + caplog.text
    assert "219-09-9999" not in response.text + caplog.text


def test_recheck_error_is_an_incomplete_assessment(checkers):
    async def guard(request, policy, stage):
        return CheckerResult(checker_id="guard", checker_version="1",
            status="error" if "[REDACTED_" in request.content else "passed")
    rules = (Rule(checker_id="pii", on_finding=Action.REDACT), Rule(checker_id="guard"))
    result = evaluate({**checkers, "guard": guard}, "Email jane@example.com", rules=rules)
    assert result.redaction.status == "withheld"
    assert result.evaluation_status == "incomplete"
    assert result.redaction.content is None


def test_secret_finding_prevents_release_of_pii_redaction(checkers):
    token = "ghp" + "_" + "A" * 36
    rules = (Rule(checker_id="pii",on_finding=Action.REDACT),Rule(checker_id="secrets",on_finding=Action.BLOCK))
    result = evaluate(checkers, "Email jane@example.com; token " + token, rules=rules)
    assert result.redaction.status == "withheld"
    assert result.redaction.content is None
    assert result.recommended_action == "block"
    assert token not in result.model_dump_json()
