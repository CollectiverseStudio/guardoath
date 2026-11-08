"""Real Engine acceptance: repeated JSON tokens must not waste Secrets' 500ms budget.

Local per-request matching reuse cannot change findings, the context-sensitive
assignment rule, the per-occurrence encoded-payload limits or fail-closed status.
All cases use synthetic content and the shipped 500ms checker deadline.
"""
import asyncio
import base64
import json

import pytest

from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, ConfidentialTerm, Policy, Rule, Stage
from guardoath.secrets_check import build_secret_checker


def _check(document, stage=Stage.INPUT, *, terms=(), assignments=True, encoded=True):
    policy = Policy(
        application_id="synthetic", version="repeated-json-v1", mode="enforce",
        timeout_ms=500, failure_action="block",
        rules=(Rule(checker_id="secrets", on_finding=Action.BLOCK),),
        detect_secret_assignments=assignments, inspect_encoded_secrets=encoded,
        confidential_terms=terms,
    )
    request = CheckRequest(
        application_id="synthetic", policy_version=policy.version,
        content_format="json", content=json.dumps(document, separators=(",", ":")),
    )
    return asyncio.run(Engine([policy], {"secrets": build_secret_checker()}).check(request, stage))


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_repeated_json_fields_keep_actual_secret_candidate_and_configured_term(stage):
    token = "ghp_" + "A" * 36
    document = {"items": [{"note": "ordinary approved text"} for _ in range(500)],
                "token_value": token, "context": "INTERNAL PROJECT"}
    result = _check(document, stage, terms=(
        ConfidentialTerm(rule_id="internal", term="internal project"),))
    assert result.evaluation_status == "complete", [
        (c.checker_id, c.status, c.error_code) for c in result.checker_results
    ]
    assert {f.rule_id for f in result.findings} == {
        "secrets.github_token", "confidential.internal"
    }
    assert result.effective_action == Action.BLOCK
    assert token not in result.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_identical_text_still_receives_field_sensitive_assignment_inspection(stage):
    # The same text first appears in a normal field, then a secret-labelled
    # field. De-duplication by text alone would silently miss this rule.
    document = {"items": [{"note": "synthetic-password-value"} for _ in range(500)],
                "password": "synthetic-password-value"}
    result = _check(document, stage)
    assert result.evaluation_status == "complete"
    assert {f.rule_id for f in result.findings} == {"secrets.credential_assignment"}
    assert result.effective_action == Action.BLOCK


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_repeated_encoded_strings_still_count_every_payload_and_fail_closed(stage):
    # Same encoded token repeated: scanning identical text once must NEVER
    # turn the original 16-occurrence inspection limit into an Allow decision.
    within = _check({"payloads": ["hex: 61"] * 16}, stage)
    assert within.evaluation_status == "complete"
    assert within.effective_action == Action.ALLOW
    over = _check({"payloads": ["hex: 61"] * 17}, stage)
    assert over.evaluation_status == "incomplete"
    assert over.effective_action == Action.BLOCK
    assert over.checker_results[0].error_code == "secret_encoding_limit"


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_repeated_encoded_secret_still_detected_without_duplicate_findings(stage):
    token = "ghp_" + "B" * 36
    encoded = "base64: " + base64.b64encode(token.encode()).decode()
    result = _check({"items": [encoded] * 3}, stage)
    assert result.evaluation_status == "complete"
    assert [f.rule_id for f in result.findings] == ["secrets.github_token"]
    assert token not in result.model_dump_json()
