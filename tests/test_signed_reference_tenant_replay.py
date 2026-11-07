"""Authenticated signed-reference replay boundaries; synthetic authority only."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.models import AdvancedTrustedReferenceRule, Stage
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore
from guardoath.trusted_reference import SIGNING_KEY_ENV, sign_reference

KEY = "synthetic-replay-host-key-012345678901234567890123456789"
SECRET = "synthetic-replay-demo-secret-0123456789"
RULE_ID = "business.permit_guard"


def stamp(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def signed(owner, *, limit="50", stages=("input", "output")):
    now = datetime.now(timezone.utc)
    rule = AdvancedTrustedReferenceRule(
        rule_id="permit_guard", kind="permit_cap",
        fields={"reported_emission": "/emission"},
        reference_values={"permit_limit": limit},
        source_id="synthetic-private-source", reference_version="private-version",
        verified_at=stamp(now - timedelta(minutes=1)),
        expires_at=stamp(now + timedelta(hours=1)), stages=stages,
        host_signature="0" * 64,
    )
    return rule.model_copy(update={"host_signature": sign_reference(rule, owner, KEY)})


def harness(rule_a, rule_b, *, off_stage=None):
    records = []
    headers = {}
    for owner, rule in (("replay-a", rule_a), ("replay-b", rule_b)):
        token = demo_key(owner, SECRET)
        headers[owner] = {"Authorization": "Bearer " + token}
        kwargs = {off_stage + "_rule_actions": {RULE_ID: "off"}} if off_stage and owner == "replay-b" else {}
        records.append(TenantRecord(
            tenant_id=owner, display_name="Synthetic replay", demo=True,
            overage="block", monthly_allowance=100, key_hashes=(key_hash(token),),
            mode="observe", failure_action="flag", input_check_cross_field="flag",
            output_check_cross_field="flag", advanced_trusted_reference=(rule,), **kwargs,
        ))
    store = MemoryTenantStore(tuple(records))
    client = TestClient(create_app(engine=demo_engine(), credentials={},
                                  tenants=TenantService(store, default_checkers(), SECRET)))
    return client, store, headers


def check(client, headers, owner, stage, *, application=None):
    return client.post("/v1/checks/" + stage, headers=headers[owner], json={
        "application_id": application or owner, "content_format": "json",
        "content": json.dumps({"emission": 60, "permit_limit": "999999",
                               "host_signature": "submitted-unsigned-override"}),
    })


def private_receipt(response, rules):
    for rule in rules:
        for value in (KEY, SECRET, rule.host_signature, rule.source_id, rule.reference_version):
            assert value not in response.text
    assert "submitted-unsigned-override" not in response.text


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("attack", ["owner", "stage", "value", "expiry", "semantic", "missing-key"])
def test_authenticated_replay_is_incomplete_block_even_observe_flag(monkeypatch, stage, attack):
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    valid = signed("replay-a")
    other = signed("replay-b")
    if attack == "owner":
        other = valid  # Valid HMAC for A, not B; identical rule ID is deliberate.
    elif attack == "stage":
        other = signed("replay-b", stages=("input",)).model_copy(update={"stages": (Stage.INPUT, Stage.OUTPUT)})
    elif attack == "value":
        other = other.model_copy(update={"reference_values": {"permit_limit": "999999"}})
    elif attack == "expiry":
        other = other.model_copy(update={"expires_at": "2020-01-01T00:00:00Z"})
    elif attack == "semantic":
        other = signed("replay-b", limit="NaN")  # Valid MAC, invalid authority value.
    client, store, headers = harness(valid, other)
    with client:
        control = check(client, headers, "replay-a", stage)
        assert control.status_code == 200
        assert control.json()["evaluation_status"] == "complete"
        assert control.json()["recommended_action"] == "flag"
        assert control.json()["effective_action"] == "allow"
        assert {f["rule_id"] for f in control.json()["findings"]} == {RULE_ID}
        if attack == "missing-key":
            monkeypatch.delenv(SIGNING_KEY_ENV)
        response = check(client, headers, "replay-b", stage)
        assert response.status_code == 200
        body = response.json()
        assert body["evaluation_status"] == "incomplete"
        assert body["recommended_action"] == body["effective_action"] == "block"
        assert {f["rule_id"] for f in body["findings"]} == {RULE_ID}
        private_receipt(response, (valid, other))
        private_receipt(control, (valid, other))
    assert store.get("replay-a").used(usage_month()) == 1
    assert store.get("replay-b").used(usage_month()) == 1


@pytest.mark.parametrize("stage", ["input", "output"])
def test_replayed_reference_off_is_stage_and_owner_local(monkeypatch, stage):
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    valid = signed("replay-a")
    client, store, headers = harness(valid, valid, off_stage=stage)
    other_stage = "output" if stage == "input" else "input"
    with client:
        off = check(client, headers, "replay-b", stage)
        assert off.status_code == 200
        assert off.json()["evaluation_status"] == "complete"
        assert off.json()["effective_action"] == "allow"
        assert off.json()["findings"] == []
        active = check(client, headers, "replay-b", other_stage)
        assert active.status_code == 200
        assert active.json()["evaluation_status"] == "incomplete"
        assert active.json()["effective_action"] == "block"
        assert {f["rule_id"] for f in active.json()["findings"]} == {RULE_ID}
        forbidden = check(client, headers, "replay-b", stage, application="replay-a")
        assert forbidden.status_code == 403
        private_receipt(forbidden, (valid,))
    assert store.get("replay-a").used(usage_month()) == 0
    assert store.get("replay-b").used(usage_month()) == 2
