"""Real local PII regression for the 500-rule tenant's repeated JSON fields.

No mocked recognizer, disabled check, synthetic larger deadline or user-data log.
The authenticated dashboard has 1,000ms tenant deadline; shipped 500ms suites
continue to run separately and remain authoritative for PII reliability.
"""
import asyncio
import json

import pytest

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage


def _check(data, stage, action=Action.BLOCK):
    policy = Policy(
        application_id="repeat-pii", version="v1", mode="enforce",
        timeout_ms=1000, failure_action="block",
        rules=(Rule(checker_id="pii", on_finding=action),),
        privacy_patterns=True, detect_labeled_sensitive_data=True,
    )
    request = CheckRequest(
        application_id=policy.application_id, policy_version=policy.version,
        content_format="json", content=json.dumps(data, separators=(",", ":")),
    )
    engine = Engine([policy], default_checkers(enable_pii=True))
    return asyncio.run(engine.check(request, stage))


def _safe_status(result):
    return [(c.checker_id, c.status, c.error_code, c.latency_ms)
            for c in result.checker_results]


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_500_repeated_benign_json_rows_keep_real_unique_pii_detection(stage):
    # Duplicate common values and keys must not monopolize the customer tenant
    # deadline or suppress the unique real email finding at the end.
    data = {"items": [{"note": "ordinary status", "unit": "kg"} for _ in range(500)],
            "contact": "synthetic@example.com"}
    result = _check(data, stage)
    assert result.evaluation_status == "complete", _safe_status(result)
    assert "pii.email_address" in {f.rule_id for f in result.findings}
    assert result.effective_action == Action.BLOCK
    assert "synthetic@example.com" not in result.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_500_repeated_clean_fields_preserve_redaction_and_mandatory_recheck(stage):
    data = {"items": [{"note": "ordinary status", "unit": "kg"} for _ in range(500)],
            "contact": "synthetic@example.com"}
    result = _check(data, stage, Action.REDACT)
    assert result.evaluation_status == "complete", _safe_status(result)
    assert {f.rule_id for f in result.findings} == {"pii.email_address"}
    assert result.redaction.status == "ready"
    assert result.redaction.content is not None
    assert "synthetic@example.com" not in result.redaction.content
    assert result.redaction.validation_results
    assert all(c.status == "passed" for c in result.redaction.validation_results)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_65_repeated_labeled_fields_still_exceed_original_budget_and_block(stage):
    # Unlike clean leaves, labeled fields MUST each consume one of the original
    # 64 inspection slots. Memoizing the identical text here would create a bypass.
    data = {"numbers": [{"phone_number": "2025550199"} for _ in range(65)]}
    result = _check(data, stage)
    assert result.evaluation_status == "incomplete"
    assert result.effective_action == Action.BLOCK
    assert result.checker_results[0].error_code == "pii_field_limit"
    assert "2025550199" not in result.model_dump_json()
