"""Measure current lexical chat detection against synthetic intent labels.

This records a known quoted-text false positive, one covered Spanish direct
command, and a separate still-unrecognized Spanish paraphrase. Passing the test is evidence of lexical behavior, not semantic
accuracy or successful contextual safety enforcement.
"""
import asyncio
import json
from pathlib import Path

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Stage
from guardoath.tenant_record import TENANT_POLICY_VERSION, TenantRecord


DATA = json.loads(
    (Path(__file__).resolve().parents[1] / "evals" / "chat-semantic-calibration.json")
    .read_text(encoding="utf-8")
)
CASES = DATA["cases"]


def _evaluate(case):
    tenant = TenantRecord(
        tenant_id="chat-cal", display_name="Chat calibration", mode="enforce",
        input_check_prompt_attack="flag",
    )
    engine = Engine([tenant.to_policy()], default_checkers())
    request = CheckRequest(
        application_id=tenant.tenant_id,
        policy_version=TENANT_POLICY_VERSION,
        content=case["text"],
    )
    return asyncio.run(engine.check(request, Stage.INPUT))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_current_lexical_chat_observation_is_recorded_without_content_leak(case):
    result = _evaluate(case)
    matched = {finding.rule_id for finding in result.findings if finding.rule_id.startswith("prompt_attack.")}
    assert matched == set(case["expected_rule_ids"])
    assert result.evaluation_status == "complete"
    assert not result.simulated
    assert result.effective_action == (Action.FLAG if matched else Action.ALLOW)
    assert case["text"] not in result.model_dump_json()


def test_synthetic_intent_confusion_matrix_documents_actual_coverage_gaps():
    predictions = [(case["label"] == "attack", bool(case["expected_rule_ids"])) for case in CASES]
    tp = sum(actual and detected for actual, detected in predictions)
    fp = sum(not actual and detected for actual, detected in predictions)
    fn = sum(actual and not detected for actual, detected in predictions)
    tn = sum(not actual and not detected for actual, detected in predictions)
    assert (tp, fp, fn, tn) == (3, 1, 1, 2)
    assert tp / (tp + fp) == pytest.approx(3 / 4)  # synthetic precision
    assert tp / (tp + fn) == pytest.approx(3 / 4)  # synthetic recall
    assert DATA["schema_version"] == 1
    assert len(CASES) == len({case["id"] for case in CASES})
