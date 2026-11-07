"""Malformed timezone offsets cannot be normalized into benign comparisons."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, AdvancedConversionTimeRule, CheckRequest, Policy, Rule, Stage
from guardoath.trusted_reference import reference_values_are_valid


def evaluate(kind, stage, start):
    if kind == "cold_chain_excursion":
        fields = {"excursion_start": "/start", "excursion_end": "/end", "allowed_minutes": "/minutes"}
        payload = {"start": start, "end": "2026-11-01T11:00:00Z", "minutes": 300}
    else:
        fields = {"order_at": "/start", "shipment_at": "/shipment", "delivery_at": "/end", "return_at": "/return"}
        payload = {"start": start, "shipment": "2026-11-01T10:00:00Z", "end": "2026-11-01T11:00:00Z"}
    rule = AdvancedConversionTimeRule(rule_id="offset_guard", kind=kind, fields=fields)
    policy = Policy(application_id="offset-tenant", version="v1", mode="enforce",
                    rules=(Rule(checker_id="cross_field", on_finding=Action.BLOCK),),
                    advanced_conversion_time=(rule,))
    request = CheckRequest(application_id="offset-tenant", policy_version="v1", content_format="json",
                           content=json.dumps(payload))
    return asyncio.run(Engine([policy], default_checkers()).check(request, stage))


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("kind", ["cold_chain_excursion", "lifecycle_chronology"])
@pytest.mark.parametrize("offset", ["+00:60", "+01:99", "-00:60"])
def test_invalid_offset_minutes_keep_finding_on_each_stage(kind, stage, offset):
    timestamp = "2026-11-01T09:00:00" + offset
    result = evaluate(kind, stage, timestamp)
    assert result.evaluation_status == "complete"
    assert result.effective_action == Action.BLOCK
    assert {f.rule_id for f in result.findings} == {"business.offset_guard"}
    assert timestamp not in str(result.findings)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("kind", ["cold_chain_excursion", "lifecycle_chronology"])
@pytest.mark.parametrize("timestamp", ["2026-11-01T09:00:00Z", "2026-11-01T09:00:00+01:59", "2026-11-01T09:00:00-00:59"])
def test_valid_offset_boundary_controls_remain_benign(kind, stage, timestamp):
    result = evaluate(kind, stage, timestamp)
    assert result.evaluation_status == "complete"
    assert result.effective_action == Action.ALLOW and result.findings == ()


@pytest.mark.parametrize("offset", ["+00:60", "+01:99", "-00:60"])
def test_invalid_offset_cannot_become_valid_signed_reference_values(offset):
    rule = SimpleNamespace(kind="medication_interval", reference_values={
        "previous_administration": "2026-11-01T09:00:00" + offset,
        "minimum_interval_minutes": "240"})
    assert not reference_values_are_valid(rule)
