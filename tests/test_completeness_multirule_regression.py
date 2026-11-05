"""Required-section completeness: independent input/output acceptance evidence.

Presence and character-count checks do not certify accuracy, meaning or safety.
"""
import asyncio
from pathlib import Path

import pytest

from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Stage

ROOT = Path(__file__).resolve().parents[1]
BASE = load_policies(ROOT / "policies" / "completeness.json")[0]
POLICY = BASE.model_copy(update={"mode": "enforce"})
ENGINE = Engine([POLICY], default_checkers())


def assess(content, stage, *, fmt="text"):
    return asyncio.run(ENGINE.check(
        CheckRequest(application_id="demo", policy_version=POLICY.version,
                     content=content, content_format=fmt), stage))


@pytest.mark.parametrize("stage,content", [
    (Stage.INPUT, "Summary: A valid request."),
    (Stage.INPUT, "summary:\nA valid request."),
    (Stage.OUTPUT, "Summary: Done.\nNext steps: Contact support."),
    (Stage.OUTPUT, "SUMMARY: Done.\nnext STEPS:\nContact support."),
    (Stage.OUTPUT, "Summary: Done.\r\nNext steps: abcde"),
])
def test_exact_literal_sections_pass_without_semantic_claim(stage, content):
    response = assess(content, stage)
    assert response.evaluation_status == "complete"
    assert response.recommended_action == "allow"
    assert response.effective_action == "allow"
    assert not response.findings
    assert any(c.checker_id == "completeness" and c.status == "passed"
               for c in response.checker_results)


@pytest.mark.parametrize("stage,content,expected", [
    (Stage.INPUT, "Details: Request.", {"completeness.request_summary"}),
    (Stage.INPUT, "Summary: Alpha\nSummary: Beta", {"completeness.request_summary"}),
    (Stage.OUTPUT, "Summary: Done.", {"completeness.next_steps"}),
    (Stage.OUTPUT, "Next steps: Contact support.", {"completeness.response_summary"}),
    (Stage.OUTPUT, "Summary: Done.\nNext steps: Hi", {"completeness.next_steps"}),
    (Stage.OUTPUT, "Summary: Done.\nNext steps: A B C D", {"completeness.next_steps"}),
    (Stage.OUTPUT, "Summary: Done.\nNext steps: First.\nNext steps: Again.",
     {"completeness.next_steps"}),
    (Stage.OUTPUT, "Details: unrelated text", {"completeness.response_summary", "completeness.next_steps"}),
])
def test_all_applicable_section_failures_survive(stage, content, expected):
    response = assess(content, stage)
    assert response.evaluation_status == "complete"
    assert {f.rule_id for f in response.findings} == expected
    assert len(response.findings) == len(expected)
    assert response.recommended_action == ("flag" if stage == Stage.INPUT else "block")
    assert response.effective_action == response.recommended_action
    assert all("unrelated text" not in f.explanation for f in response.findings)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_wrong_message_format_fails_closed(stage):
    response = assess('{"Summary":"Done","Next steps":"Contact support"}', stage, fmt="json")
    assert response.evaluation_status == "incomplete"
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert not response.findings
    assert any(c.checker_id == "completeness" and c.status == "skipped"
               for c in response.checker_results)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_unknown_label_ends_previous_body_and_cannot_rescue_short_section(stage):
    if stage == Stage.INPUT:
        content = "Summary: \nOther: Large unrelated body."
        expected = {"completeness.request_summary"}
    else:
        content = "Summary: Done.\nNext steps: Hi\nOther: Large unrelated body."
        expected = {"completeness.next_steps"}
    response = assess(content, stage)
    assert response.evaluation_status == "complete"
    assert {f.rule_id for f in response.findings} == expected
    assert response.recommended_action == ("flag" if stage == Stage.INPUT else "block")
    assert response.effective_action == response.recommended_action
