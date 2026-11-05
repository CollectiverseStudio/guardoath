"""Independent timestamp ordering regressions for synthetic local JSON.

These tests verify chronological comparison of instants, not source authenticity,
clock accuracy, time-zone authorization, or cross-request history.
"""
import asyncio
import json
from pathlib import Path

import pytest

from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Stage


ROOT = Path(__file__).resolve().parents[1]
ENGINE = Engine(load_policies(ROOT / "policies" / "timestamps-batch.json"), default_checkers())


def assess(content, stage, *, fmt="json"):
    return asyncio.run(ENGINE.check(
        CheckRequest(
            application_id="demo",
            policy_version="timestamps-enforce-v1",
            content=content if isinstance(content, str) else json.dumps(content),
            content_format=fmt,
        ),
        stage,
    ))


def samples(*stamps):
    return {"samples": [
        {"timestamp": stamp, "value": position} for position, stamp in enumerate(stamps)
    ]}


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("document", [
    samples("2026-11-04T10:00:00+01:00", "2026-11-04T09:00:01Z"),
    samples("2026-11-04T09:00:00Z", "2026-11-04T05:00:01-04:00"),
    samples("2026-11-04T09:00:00.000000Z", "2026-11-04T09:00:00.000001Z"),
])
def test_increasing_instants_pass_without_reordering(stage, document):
    response = assess(document, stage)
    assert response.evaluation_status == "complete"
    assert not response.findings
    assert response.recommended_action == "allow"
    assert response.effective_action == "allow"
    assert any(c.checker_id == "timestamps" and c.status == "passed" for c in response.checker_results)


@pytest.mark.parametrize("stage,action", [(Stage.INPUT, "flag"), (Stage.OUTPUT, "block")])
@pytest.mark.parametrize("document", [
    samples("2026-11-04T09:00:00Z", "2026-11-04T10:00:00+01:00"),
    samples("2026-11-04T09:00:01Z", "2026-11-04T10:00:00+01:00"),
    samples("2026-11-04T09:00:00Z", "2026-11-04T09:00:02Z", "2026-11-04T09:00:01Z"),
])
def test_equal_or_regressing_instants_flag_without_sorting(stage, action, document):
    response = assess(document, stage)
    assert response.mode == "enforce"
    assert response.evaluation_status == "complete"
    assert {f.rule_id for f in response.findings} == {"structure.timestamp_order"}
    assert response.recommended_action == action
    assert response.effective_action == action


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("content", [
    '{"samples":[{"timestamp":"2026-11-04T09:00:00Z","value":1},{"timestamp":"2026-11-04T09:00:01Z","value":2}],"samples":[]}',
    '{"samples":[{"timestamp":"2026-11-04T09:00:00Z","value":1},',
    samples("2026-02-30T09:00:00Z", "2026-03-01T09:00:00Z"),
    {"samples":[{"timestamp":"2026-11-04T09:00:00Z","value":True},
                {"timestamp":"2026-11-04T09:00:01Z","value":1}]},
])
def test_invalid_or_ambiguous_timestamp_input_fails_closed(stage, content):
    response = assess(content, stage)
    assert response.evaluation_status == "incomplete"
    assert not response.findings
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert any(c.checker_id == "timestamps" and c.status == "error"
               and c.error_code == "timestamps_invalid_document" for c in response.checker_results)
    assert "2026-02-30" not in response.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_text_content_cannot_masquerade_as_valid_timestamp_json(stage):
    response = assess("at 09:00Z then 09:01Z", stage, fmt="text")
    assert response.evaluation_status == "incomplete"
    assert not response.findings
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert any(c.checker_id == "timestamps" and c.status == "skipped"
               and c.error_code == "timestamps_json_required" for c in response.checker_results)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_equal_instants_are_allowed_only_by_opt_in_policy(stage):
    document = samples("2026-11-04T09:00:00Z", "2026-11-04T10:00:00+01:00")
    response = asyncio.run(ENGINE.check(
        CheckRequest(
            application_id="demo",
            policy_version="timestamps-equal-v1",
            content=json.dumps(document),
            content_format="json",
        ), stage,
    ))
    assert response.mode == "observe"
    assert response.evaluation_status == "complete"
    assert not response.findings
    assert response.recommended_action == "allow"
    assert response.effective_action == "allow"


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("count,expected_status,expected_error", [
    (200, "passed", None),
    (201, "error", "timestamps_sample_limit"),
])
def test_configured_sample_budget_has_exact_boundary(stage, count, expected_status, expected_error):
    from datetime import datetime, timedelta, timezone
    origin = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
    document = samples(*(origin + timedelta(seconds=n)).isoformat().replace("+00:00", "Z")
                         for n in range(count)))
    response = assess(document, stage)
    assert response.evaluation_status == ("complete" if expected_error is None else "incomplete")
    assert response.recommended_action == ("allow" if expected_error is None else "block")
    assert response.effective_action == ("allow" if expected_error is None else "block")
    assert not response.findings
    assert any(c.checker_id == "timestamps" and c.status == expected_status
               and c.error_code == expected_error for c in response.checker_results)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("invalid", [
    "2026-11-04T09:00:00",
    "2026-11-04T09:00:00-00:00",
    "2026-11-04T09:00:00+25:00",
    "2026-11-04T09:00:00.1234567Z",
])
def test_invalid_offsets_or_precision_fail_closed(stage, invalid):
    response = assess(samples(invalid, "2026-11-04T10:00:00Z"), stage)
    assert response.evaluation_status == "incomplete"
    assert not response.findings
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert any(c.checker_id == "timestamps" and c.status == "error"
               and c.error_code == "timestamps_invalid_document"
               for c in response.checker_results)
    assert invalid not in response.model_dump_json()
