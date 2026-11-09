"""Schema Input/Output must return bounded, private, independent violations."""
import asyncio
import json

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage


SCHEMA = {
    "type": "object",
    "properties": {
        "private_age": {"type": "integer", "minimum": 18},
        "private_code": {"type": "string", "minLength": 4},
    },
    "required": ["private_age", "private_code"],
    "additionalProperties": False,
}


def check(content, stage):
    policy = Policy(
        application_id="schema-multiple", version="v1", mode="enforce",
        rules=(Rule(checker_id="schema", on_finding=Action.BLOCK),),
        input_schema=SCHEMA, output_schema=SCHEMA,
    )
    request = CheckRequest(
        application_id="schema-multiple", policy_version="v1",
        content=json.dumps(content), content_format="json",
    )
    return asyncio.run(Engine([policy], default_checkers()).check(request, stage))


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_schema_returns_every_independent_violation_without_private_fields(stage):
    bad = {"private_age": 15, "private_code": "x", "hidden_secret": "PRIVATE_MARKER_987"}
    result = check(bad, stage)
    assert result.evaluation_status == "complete"
    schema_findings = [f for f in result.findings if f.rule_id.startswith("schema.")]
    assert len(schema_findings) == 3
    assert {f.rule_id for f in schema_findings} == {"schema.invalid"}
    assert result.effective_action == Action.BLOCK
    serialized = result.model_dump_json()
    for private in ("private_age", "private_code", "hidden_secret", "PRIVATE_MARKER_987"):
        assert private not in serialized
    clean = check({"private_age": 18, "private_code": "abcd"}, stage)
    assert clean.evaluation_status == "complete"
    assert not [f for f in clean.findings if f.rule_id.startswith("schema.")]
    assert clean.effective_action == Action.ALLOW


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_schema_error_reporting_is_bounded_without_silently_passing(stage):
    many = {"type": "object", "properties": {
        f"field_{i}": {"type": "integer"} for i in range(70)
    }, "required": [f"field_{i}" for i in range(70)]}
    policy = Policy(
        application_id="schema-many", version="v1", mode="enforce",
        rules=(Rule(checker_id="schema", on_finding=Action.BLOCK),),
        input_schema=many, output_schema=many,
    )
    request = CheckRequest(
        application_id="schema-many", policy_version="v1",
        content="{}", content_format="json",
    )
    result = asyncio.run(Engine([policy], default_checkers()).check(request, stage))
    assert result.evaluation_status == "complete"
    schema_findings = [f for f in result.findings if f.rule_id.startswith("schema.")]
    assert len(schema_findings) == 65
    assert sum(f.rule_id == "schema.invalid" for f in schema_findings) == 64
    assert schema_findings[-1].rule_id == "schema.too_many_errors"
    assert result.effective_action == Action.BLOCK
    for i in range(70):
        assert f"field_{i}" not in result.model_dump_json()
