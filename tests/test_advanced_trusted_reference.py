"""FC-044/045/047/049: host-signed reference enforcement in the real Engine.

Only synthetic reference snapshots. The host key never appears in a response.
"""
from datetime import datetime, timedelta, timezone
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.hosted import TenantService
from guardoath.models import Action, AdvancedTrustedReferenceRule, CheckRequest, Policy, Rule, Stage
from guardoath.record_rules import record_rules
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore
from guardoath.trusted_reference import SIGNING_KEY_ENV, sign_reference, reference_is_trusted

KEY = "synthetic-host-operator-key-not-for-production-01234567890123456789"
TENANT = "reference-a"


def stamp(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def make(kind, *, tenant=TENANT):
    now = datetime.now(timezone.utc)
    values = {
        "dose_ceiling": ({"dose_mg": "/dose_mg"},
                         {"verified_weight_kg": "70", "max_mg_per_kg": "10"}),
        "medication_interval": ({"administered_at": "/administered_at"},
                                {"previous_administration": "2026-11-01T09:00:00Z",
                                 "minimum_interval_minutes": "240"}),
        "permit_cap": ({"reported_emission": "/reported_emission"},
                       {"permit_limit": "50"}),
        "invoice_po_vendor": ({"invoice_vendor_id": "/invoice_vendor_id"},
                              {"approved_vendor_id": "VENDOR1"}),
    }
    fields, refs = values[kind]
    draft = AdvancedTrustedReferenceRule(
        rule_id=kind, kind=kind, fields=fields, reference_values=refs,
        source_id="host-verified-reference", reference_version="v1",
        verified_at=stamp(now - timedelta(minutes=1)),
        expires_at=stamp(now + timedelta(days=1)), host_signature="0" * 64,
    )
    return draft.model_copy(update={"host_signature": sign_reference(draft, tenant, KEY)})


CASES = (
    ("FC-044", "dose_ceiling", {"dose_mg": 900}, {"dose_mg": 700}),
    ("FC-045", "medication_interval", {"administered_at": "2026-11-01T10:00:00Z"},
     {"administered_at": "2026-11-01T13:00:00Z"}),
    ("FC-047", "permit_cap", {"reported_emission": 60}, {"reported_emission": 50}),
    ("FC-049", "invoice_po_vendor", {"invoice_vendor_id": "VENDOR2"}, {"invoice_vendor_id": "VENDOR1"}),
)


def check(rules, payload, *, stage=Stage.INPUT, mode="enforce", failure_action="block",
          tenant=TENANT, rule_actions=None):
    policy = Policy(
        application_id=tenant, version="v1", mode=mode, failure_action=failure_action,
        rules=(Rule(checker_id="cross_field", on_finding=Action.FLAG),),
        advanced_trusted_reference=tuple(rules),
        input_rule_actions=rule_actions or {},
    )
    engine = Engine([policy], default_checkers())
    request = CheckRequest(application_id=tenant, policy_version="v1",
                           content=json.dumps(payload), content_format="json")
    return asyncio.run(engine.check(request, stage))


@pytest.mark.parametrize("name,kind,bad,good", CASES, ids=[r[0] for r in CASES])
def test_four_host_signed_rules_produce_correct_findings_on_both_stages(monkeypatch, name, kind, bad, good):
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    item = make(kind)
    assert reference_is_trusted(item, TENANT)
    for stage in (Stage.INPUT, Stage.OUTPUT):
        flagged = check((item,), bad, stage=stage)
        assert flagged.evaluation_status == "complete", name
        assert {f.rule_id for f in flagged.findings} == {"business." + kind}
        assert flagged.effective_action == Action.FLAG
        clean = check((item,), good, stage=stage)
        assert clean.evaluation_status == "complete", name
        assert clean.effective_action == Action.ALLOW and not clean.findings


@pytest.mark.parametrize("name,kind,bad,good", CASES, ids=[r[0] for r in CASES])
def test_missing_and_wrong_type_fail_as_configured_findings(monkeypatch, name, kind, bad, good):
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    item = make(kind)
    field = next(iter(item.fields))
    for payload in ({}, {field: True}, {field: "invalid"}):
        result = check((item,), payload)
        assert result.evaluation_status == "complete", name
        assert "business." + kind in {f.rule_id for f in result.findings}


def test_tamper_wrong_tenant_missing_key_and_expiry_always_block_even_observe_flag(monkeypatch):
    item = make("permit_cap")
    for changed in (item.model_copy(update={"reference_values": {"permit_limit": "1000"}}),
                    item.model_copy(update={"host_signature": "f" * 64}),
                    item.model_copy(update={"expires_at": "2026-01-01T00:00:00Z"})):
        monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
        result = check((changed,), {"reported_emission": 60}, mode="observe", failure_action="flag")
        assert result.evaluation_status == "incomplete"
        assert result.recommended_action == result.effective_action == Action.BLOCK
        assert "1000" not in str(result.findings)
    wrong = check((item,), {"reported_emission": 60}, tenant="other-a",
                  mode="observe", failure_action="flag")
    assert wrong.effective_action == Action.BLOCK
    monkeypatch.delenv(SIGNING_KEY_ENV)
    missing = check((item,), {"reported_emission": 60}, mode="observe", failure_action="flag")
    assert missing.evaluation_status == "incomplete" and missing.effective_action == Action.BLOCK


def test_explicit_tenant_off_keeps_unverified_reference_rule_off(monkeypatch):
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
    item = make("permit_cap")
    result = check((item,), {"reported_emission": 100}, mode="observe", failure_action="flag",
                   rule_actions={"business.permit_cap": "off"})
    assert result.evaluation_status == "complete" and result.effective_action == Action.ALLOW
    assert result.findings == ()


def test_multi_rule_mixed_finding_and_untrusted_block_retains_all_findings(monkeypatch):
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    dose, permit = make("dose_ceiling"), make("permit_cap")
    permit = permit.model_copy(update={"host_signature": "f" * 64})
    result = check((dose, permit), {"dose_mg": 900, "reported_emission": 60},
                   mode="observe", failure_action="flag")
    assert {finding.rule_id for finding in result.findings} == {
        "business.dose_ceiling", "business.permit_cap"}
    assert result.evaluation_status == "incomplete" and result.effective_action == Action.BLOCK


def test_reference_config_is_private_in_dashboard_metadata(monkeypatch):
    from types import SimpleNamespace
    item = make("invoice_po_vendor")
    records = record_rules(SimpleNamespace(comparisons=(), advanced_arithmetic=(),
                                           advanced_identity=(), advanced_conversion_time=(),
                                           advanced_trusted_reference=(item,), required_sections=()))
    entry = records["business.invoice_po_vendor"]
    assert entry["record_rule"] and entry["example"] is None
    text = json.dumps(entry)
    assert "VENDOR1" not in text and item.host_signature not in text and item.source_id not in text


def test_signed_reference_cannot_be_reused_for_other_tenant_or_changed_stage(monkeypatch):
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    item = make("invoice_po_vendor")
    assert not reference_is_trusted(item, "another-tenant")
    assert not reference_is_trusted(item.model_copy(update={"stages": (Stage.INPUT,)}), TENANT)


def test_real_authenticated_tenant_input_output_actions_and_usage(monkeypatch):
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    item = make("permit_cap")
    secret = "signed-reference-tenant-demo-secret-0123456789"
    token = demo_key(TENANT, secret)
    original = TenantRecord(
        tenant_id=TENANT, display_name="Signed reference", demo=True,
        overage="block", monthly_allowance=20,
        key_hashes=(key_hash(token),), input_check_text_security="flag",
        output_check_text_security="flag",
    )
    store = MemoryTenantStore((original,))
    api = TestClient(create_app(engine=demo_engine(), credentials={},
                                tenants=TenantService(store, default_checkers(), secret)))
    headers = {"Authorization": "Bearer " + token}
    saved = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": store.get(TENANT).revision,
        "changes": {
            "advanced_trusted_reference": [item.model_dump(mode="json")],
            "input_check_cross_field": "block", "output_check_cross_field": "flag",
        }})
    assert saved.status_code == 200, saved.text[:250]
    assert "business.permit_cap" in {r["rule_id"] for r in saved.json()["record_rules"]}
    for stage, expected in (("input", "block"), ("output", "flag")):
        response = api.post("/v1/checks/" + stage, headers=headers, json={
            "application_id": TENANT, "content_format": "json",
            "content": '{"reported_emission":60}'})
        assert response.status_code == 200, response.text[:250]
        data = response.json()
        assert data["evaluation_status"] == "complete"
        assert data["effective_action"] == expected
        assert "business.permit_cap" in {f["rule_id"] for f in data["findings"]}
        assert item.host_signature not in response.text
    assert store.get(TENANT).used(usage_month()) == 2


def test_invalid_reference_contracts_fail_configuration():
    item = make("permit_cap")
    with pytest.raises(ValidationError):
        AdvancedTrustedReferenceRule(**{**item.model_dump(), "reference_values": {}})
    with pytest.raises(ValidationError):
        AdvancedTrustedReferenceRule(**{**item.model_dump(), "host_signature": "bad"})


def test_host_reference_dashboard_status_is_private_safe_and_tenant_scoped(monkeypatch):
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    item = make("permit_cap")
    secret = "synthetic-status-dashboard-key-0123456789"
    records = []
    headers = {}
    for name in (TENANT, "reference-b"):
        token = demo_key(name, secret)
        headers[name] = {"Authorization": "Bearer " + token}
        records.append(TenantRecord(
            tenant_id=name, display_name=name, demo=True, overage="block",
            monthly_allowance=20, key_hashes=(key_hash(token),),
            input_check_text_security="flag", output_check_text_security="flag",
            advanced_trusted_reference=(item,) if name == TENANT else (),
        ))
    store = MemoryTenantStore(tuple(records))
    api = TestClient(create_app(engine=demo_engine(), credentials={},
                                tenants=TenantService(store, default_checkers(), secret)))
    assert api.get("/v1/applications/current").status_code in (401, 403)
    response = api.get("/v1/applications/current", headers=headers[TENANT])
    assert response.status_code == 200
    assert response.headers.get("cache-control") in (None, "no-store")
    status = response.json()["tenant"]["advanced_trusted_reference_status"]
    assert status == [{
        "rule_id": "permit_cap", "kind": "permit_cap",
        "stages": ["input", "output"], "snapshot_present": True,
        "runtime_integrity": "accepted",
    }]
    assert "permit_limit" not in json.dumps(status)
    for private in ("host-verified-reference", item.host_signature,
                    item.reference_values["permit_limit"], item.verified_at,
                    item.expires_at, KEY):
        assert private not in json.dumps(status)
    neighbor = api.get("/v1/applications/current", headers=headers["reference-b"])
    assert neighbor.status_code == 200
    assert neighbor.json()["tenant"]["advanced_trusted_reference_status"] == []


def test_authenticated_runtime_integrity_status_tracks_host_key_and_tampering(monkeypatch):
    """Status uses Engine's actual trust gate; a saved snapshot alone is never 'ready'."""
    good = make("permit_cap")
    tampered = make("invoice_po_vendor").model_copy(update={"host_signature": "f" * 64})
    secret = "synthetic-runtime-integrity-demo-secret-0123456789"
    token = demo_key(TENANT, secret)
    record = TenantRecord(
        tenant_id=TENANT, display_name="Reference status", demo=True,
        monthly_allowance=20, overage="block", key_hashes=(key_hash(token),),
        input_check_text_security="flag",
        advanced_trusted_reference=(good, tampered),
    )
    store = MemoryTenantStore((record,))
    headers = {"Authorization": "Bearer " + token}
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    with TestClient(create_app(engine=demo_engine(), credentials={},
                               tenants=TenantService(store, default_checkers(), secret))) as api:
        def statuses():
            response = api.get("/v1/applications/current", headers=headers)
            assert response.status_code == 200
            assert response.headers.get("cache-control") in (None, "no-store")
            rows = response.json()["tenant"]["advanced_trusted_reference_status"]
            serialized = json.dumps(rows)
            for value in (KEY, good.host_signature, good.source_id,
                          good.verified_at, good.expires_at, "50", "VENDOR1"):
                assert value not in serialized
            return {item["rule_id"]: item["runtime_integrity"] for item in rows}

        assert statuses() == {"permit_cap": "accepted", "invoice_po_vendor": "blocked"}
        monkeypatch.setenv(SIGNING_KEY_ENV, "different-host-key-for-synthetic-testing-only")
        assert statuses() == {"permit_cap": "blocked", "invoice_po_vendor": "blocked"}
        monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
        assert statuses()["permit_cap"] == "accepted"
        monkeypatch.delenv(SIGNING_KEY_ENV)
        assert statuses() == {"permit_cap": "blocked", "invoice_po_vendor": "blocked"}
        assert store.get(TENANT).used(usage_month()) == 0
