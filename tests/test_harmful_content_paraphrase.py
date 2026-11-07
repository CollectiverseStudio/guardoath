"""Paraphrase regressions for known lexical misses in harmful-content checks.

These are bounded explicit wording variants, NOT evidence of semantic understanding.
Preserve tenant action behavior, safe look-alikes and per-stage findings privacy.
"""
import asyncio

import pytest

from guardoath.harmful_content import inspect
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage


THREAT_VARIANTS = (
    "I plan to injure you.",
    "I intend to harm you.",
    "I am planning to attack you.",
    "I'm planning on hurting you.",
)
GROUP_VIOLENCE_VARIANTS = (
    "Murder all refugees.",
    "Massacre the immigrants.",
)
BENIGN_LOOKALIKES = (
    "I intend to help you.",
    "I plan to join you for lunch.",
    "I am planning to attack the problem systematically.",
    "The article discusses how researchers document threats of violence.",
    "We must protect refugees and prevent violence.",
    "Never threaten to hurt people.",
)


@pytest.mark.parametrize("content", THREAT_VARIANTS)
def test_common_direct_threat_paraphrases_preserve_rule_identity(content):
    assert "harmful_content.threat_to_hurt_you" in inspect(content)


@pytest.mark.parametrize("content", GROUP_VIOLENCE_VARIANTS)
def test_more_group_violence_verbs_detected(content):
    assert "harmful_content.hate_violence_call" in inspect(content)


@pytest.mark.parametrize("content", BENIGN_LOOKALIKES)
def test_nonthreatening_educational_or_ordinary_context_not_newly_blocked(content):
    assert inspect(content) == ()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_same_paraphrase_reports_on_both_stages_with_tenant_actions(stage):
    policy = Policy(application_id="owner-a", version="v1", mode="enforce",
                    rules=(Rule(checker_id="harmful_content",
                                on_finding=Action.FLAG,
                                on_input_finding=Action.BLOCK,
                                on_output_finding=Action.FLAG),))
    request = CheckRequest(application_id="owner-a", policy_version="v1",
                           content="I plan to injure you.", content_format="text")
    result = asyncio.run(Engine([policy], default_checkers()).check(request, stage))
    assert result.evaluation_status == "complete"
    assert {finding.rule_id for finding in result.findings} == {
        "harmful_content.threat_to_hurt_you"
    }
    assert result.effective_action == (Action.BLOCK if stage == Stage.INPUT else Action.FLAG)
    assert "injure" not in result.model_dump_json()


def test_negative_case_does_not_get_a_fake_semantic_finding():
    # A caution against claiming broad paraphrase coverage based on a small
    # fixed pattern inventory; this remains a lexical detector.
    assert inspect("The fictional story includes a dangerous antagonist.") == ()
