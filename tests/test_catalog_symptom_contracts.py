"""Structured symptom intake contracts, not medical diagnosis or triage (UC043)."""
import asyncio
import json
import pytest
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage

SCHEMA = {
    "type": "object", "required": ["symptom", "duration_days", "severity"],
    "additionalProperties": False,
    "properties": {
        "symptom": {"type": "string", "minLength": 1, "maxLength": 200},
        "duration_days": {"type": "integer", "minimum": 0, "maximum": 3650},
        "severity": {"type": "integer", "minimum": 0, "maximum": 10},
    },
}

@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("record,valid", [
    ({"symptom": "cough", "duration_days": 2, "severity": 4}, True),
    ({"symptom": "headache", "duration_days": 0, "severity": 0}, True),
    ({"symptom": "", "duration_days": 2, "severity": 4}, False),
    ({"symptom": "cough", "severity": 4}, False),
    ({"symptom": "cough", "duration_days": -1, "severity": 4}, False),
    ({"symptom": "cough", "duration_days": 2, "severity": 11}, False),
    ({"symptom": "cough", "duration_days": "two", "severity": 4}, False),
    ({"symptom": "cough", "duration_days": 2, "severity": 4, "extra": True}, False),
])
def test_uc043_structured_symptom_intake_not_clinical_assessment(stage, record, valid):
    policy = Policy(application_id="symptom-demo", version="v1", mode="enforce",
                    rules=(Rule(checker_id="schema", on_finding=Action.BLOCK),),
                    input_schema=SCHEMA, output_schema=SCHEMA)
    result = asyncio.run(Engine([policy], default_checkers()).check(CheckRequest(
        application_id="symptom-demo", policy_version="v1",
        content_format="json", content=json.dumps(record)), stage))
    assert result.evaluation_status == "complete"
    assert result.effective_action == (Action.ALLOW if valid else Action.BLOCK)
    assert bool(result.findings) is not valid
