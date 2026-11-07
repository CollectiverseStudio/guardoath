"""Regression for independent findings and tenant-owned stage decisions.

These tests intentionally exercise the existing lexical checker, not a semantic classifier.
"""
import asyncio

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Stage
from guardoath.tenant_record import TENANT_POLICY_VERSION, TenantRecord


TEXT = "I will hurt you tomorrow. You are an idiot."


def evaluate(record, stage, content=TEXT):
    engine = Engine([record.to_policy()], default_checkers())
    request = CheckRequest(
        application_id=record.tenant_id,
        policy_version=TENANT_POLICY_VERSION,
        content=content,
    )
    return asyncio.run(engine.check(request, stage))


@pytest.mark.parametrize(
    "stage,setting,expected",
    [
        (Stage.INPUT, "flag", Action.FLAG),
        (Stage.INPUT, "block", Action.BLOCK),
        (Stage.OUTPUT, "flag", Action.FLAG),
        (Stage.OUTPUT, "block", Action.BLOCK),
    ],
)
def test_two_independent_findings_survive_stage_action(stage, setting, expected):
    record = TenantRecord(
        tenant_id="multi-findings",
        display_name="Multi findings",
        mode="enforce",
        input_check_harmful_content=setting,
        output_check_harmful_content=setting,
    )
    result = evaluate(record, stage)
    rule_ids = {finding.rule_id for finding in result.findings}
    assert {
        "harmful_content.threat_to_hurt_you",
        "harmful_content.targeted_insult",
    } <= rule_ids
    assert result.effective_action == expected
    dumped = result.model_dump_json()
    assert TEXT not in dumped


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_stage_disabled_does_not_run_harmful_content(stage):
    record = TenantRecord(
        tenant_id="stage-off",
        display_name="Stage off",
        mode="enforce",
        input_check_harmful_content="off" if stage == Stage.INPUT else "block",
        output_check_harmful_content="off" if stage == Stage.OUTPUT else "block",
    )
    result = evaluate(record, stage)
    assert not any(f.rule_id.startswith("harmful_content.") for f in result.findings)
    assert result.effective_action == Action.ALLOW


def test_tenant_isolation_for_same_payload():
    blocked = TenantRecord(
        tenant_id="tenant-block",
        display_name="Blocking tenant",
        mode="enforce",
        input_check_harmful_content="block",
    )
    flagged = TenantRecord(
        tenant_id="tenant-flag",
        display_name="Flagging tenant",
        mode="enforce",
        input_check_harmful_content="flag",
    )
    assert evaluate(blocked, Stage.INPUT).effective_action == Action.BLOCK
    assert evaluate(flagged, Stage.INPUT).effective_action == Action.FLAG
