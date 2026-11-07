"""A valid MAC must not turn malformed reference values into trusted authority."""
import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, AdvancedTrustedReferenceRule, CheckRequest, Policy, Rule, Stage
from guardoath.trusted_reference import SIGNING_KEY_ENV, reference_is_trusted, sign_reference

KEY = "synthetic-reference-value-validation-key-01234567890123456789"
TENANT = "synthetic-reference-values"
CASES = [
    ("dose_ceiling", {"dose_mg": "/dose"}, {"verified_weight_kg": "0", "max_mg_per_kg": "10"}, {"dose": 1}),
    ("dose_ceiling", {"dose_mg": "/dose"}, {"verified_weight_kg": "70", "max_mg_per_kg": "NaN"}, {"dose": 1}),
    ("permit_cap", {"reported_emission": "/emission"}, {"permit_limit": "NaN"}, {"emission": 1}),
    ("permit_cap", {"reported_emission": "/emission"}, {"permit_limit": "-1"}, {"emission": 1}),
    ("permit_cap", {"reported_emission": "/emission"}, {"permit_limit": "1e999"}, {"emission": 1}),
    ("medication_interval", {"administered_at": "/administered"},
     {"previous_administration": "invalid", "minimum_interval_minutes": "60"}, {"administered": "2026-11-01T13:00:00Z"}),
    ("medication_interval", {"administered_at": "/administered"},
     {"previous_administration": "2026-11-01T09:00:00Z", "minimum_interval_minutes": "0"}, {"administered": "2026-11-01T13:00:00Z"}),
    ("medication_interval", {"administered_at": "/administered"},
     {"previous_administration": "2026-11-01T09:00:00Z", "minimum_interval_minutes": "060"}, {"administered": "2026-11-01T13:00:00Z"}),
    ("invoice_po_vendor", {"invoice_vendor_id": "/vendor"},
     {"approved_vendor_id": "PRIVATE INVALID VENDOR"}, {"vendor": "VENDOR1"}),
]


def signed(case):
    kind, fields, references, _ = case
    now = datetime.now(timezone.utc)
    item = AdvancedTrustedReferenceRule(
        rule_id="reference_guard", kind=kind, fields=fields, reference_values=references,
        source_id="synthetic-source", reference_version="v1",
        verified_at=(now - timedelta(minutes=1)).isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(), host_signature="0" * 64,
    )
    return item.model_copy(update={"host_signature": sign_reference(item, TENANT, KEY)})


@pytest.mark.parametrize("case", CASES, ids=[f"{i}-{x[0]}" for i, x in enumerate(CASES)])
@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_malformed_signed_reference_is_incomplete_block_even_observe_flag(monkeypatch, case, stage):
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    item = signed(case)
    assert not reference_is_trusted(item, TENANT)
    policy = Policy(application_id=TENANT, version="v1", mode="observe", failure_action="flag",
                    rules=(Rule(checker_id="cross_field", on_finding=Action.FLAG),),
                    advanced_trusted_reference=(item,))
    request = CheckRequest(application_id=TENANT, policy_version="v1", content_format="json",
                           content=json.dumps(case[3]))
    result = asyncio.run(Engine([policy], default_checkers()).check(request, stage))
    assert result.evaluation_status == "incomplete"
    assert result.recommended_action == result.effective_action == Action.BLOCK
    assert {f.rule_id for f in result.findings} == {"business.reference_guard"}
    assert "PRIVATE INVALID VENDOR" not in result.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_explicit_off_keeps_malformed_reference_rule_unselected(monkeypatch, stage):
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    item = signed(CASES[2])
    policy = Policy(application_id=TENANT, version="v1", mode="observe", failure_action="flag",
                    rules=(Rule(checker_id="cross_field", on_finding=Action.FLAG),),
                    input_rule_actions={"business.reference_guard": "off"},
                    output_rule_actions={"business.reference_guard": "off"},
                    advanced_trusted_reference=(item,))
    request = CheckRequest(application_id=TENANT, policy_version="v1", content_format="json",
                           content=json.dumps(CASES[2][3]))
    result = asyncio.run(Engine([policy], default_checkers()).check(request, stage))
    assert result.evaluation_status == "complete"
    assert result.effective_action == Action.ALLOW
    assert result.findings == ()
