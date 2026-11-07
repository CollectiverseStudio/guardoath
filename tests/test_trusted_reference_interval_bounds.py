"""A signed snapshot must have a representable earliest administration time."""
from datetime import datetime, timedelta, timezone
import asyncio
import json

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, AdvancedTrustedReferenceRule, CheckRequest, Policy, Rule, Stage
from guardoath.trusted_reference import SIGNING_KEY_ENV, reference_is_trusted, sign_reference

KEY = "synthetic-interval-boundary-host-key-01234567890123456789"
TENANT = "interval-boundary"


def reference(previous):
    now = datetime.now(timezone.utc)
    item = AdvancedTrustedReferenceRule(
        rule_id="interval_guard", kind="medication_interval",
        fields={"administered_at": "/time"},
        reference_values={"previous_administration": previous, "minimum_interval_minutes": "240"},
        source_id="synthetic-interval-source", reference_version="v1",
        verified_at=(now-timedelta(minutes=1)).isoformat(),
        expires_at=(now+timedelta(hours=1)).isoformat(), host_signature="0"*64,
    )
    return item.model_copy(update={"host_signature": sign_reference(item, TENANT, KEY)})


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("off", [False, True])
def test_interval_overflow_is_untrusted_block_except_explicit_off(monkeypatch, stage, off):
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    item = reference("9999-12-31T23:59:59Z")
    assert not reference_is_trusted(item, TENANT)
    actions = {"business.interval_guard": "off"} if off else {}
    policy = Policy(application_id=TENANT, version="v1", mode="observe", failure_action="flag",
                    rules=(Rule(checker_id="cross_field", on_finding=Action.FLAG),),
                    advanced_trusted_reference=(item,), input_rule_actions=actions,
                    output_rule_actions=actions)
    request = CheckRequest(application_id=TENANT, policy_version="v1", content_format="json",
                           content=json.dumps({"time": "9999-12-31T23:59:59Z"}))
    result = asyncio.run(Engine([policy], default_checkers()).check(request, stage))
    assert result.evaluation_status == ("complete" if off else "incomplete")
    assert result.effective_action == (Action.ALLOW if off else Action.BLOCK)
    assert result.recommended_action == (Action.ALLOW if off else Action.BLOCK)
    assert {f.rule_id for f in result.findings} == (set() if off else {"business.interval_guard"})
    assert KEY not in str(result.findings) and item.host_signature not in str(result.findings)


def test_latest_representable_interval_boundary_remains_trusted(monkeypatch):
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    item = reference("9999-12-31T19:59:59Z")
    assert reference_is_trusted(item, TENANT)
