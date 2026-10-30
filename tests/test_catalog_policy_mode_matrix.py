"""Policy-mode contract: observe findings must not masquerade as enforcement.

Tests the current public engine behavior only; no production checker changes.
"""
import asyncio
import json

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage

SCHEMA = {"type": "object", "required": ["id"], "additionalProperties": False,
          "properties": {"id": {"type": "integer", "minimum": 1}}}


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("mode", ["observe", "enforce"])
@pytest.mark.parametrize("payload,violates", [
    ({"id": 1}, False),
    ({"id": 0}, True),
    ({"id": "1"}, True),
    ({}, True),
    ({"id": 1, "extra": True}, True),
])
def test_schema_policy_mode_changes_effective_action_not_findings(stage, mode, payload, violates):
    policy = Policy(application_id="mode-contract", version="v1", mode=mode,
                    rules=(Rule(checker_id="schema", on_finding=Action.BLOCK),),
                    input_schema=SCHEMA, output_schema=SCHEMA)
    result = asyncio.run(Engine([policy], default_checkers()).check(CheckRequest(
        application_id="mode-contract", policy_version="v1",
        content_format="json", content=json.dumps(payload)), stage))
    assert result.evaluation_status == "complete"
    assert bool(result.findings) is violates
    assert result.recommended_action == (Action.BLOCK if violates else Action.ALLOW)
    assert result.effective_action == (
        Action.BLOCK if violates and mode == "enforce" else Action.ALLOW)
