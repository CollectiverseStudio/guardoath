"""The control registry: every tenant-record check described once, with examples that behave as described.

Each example runs with the demo user settings and only its own control on. Positive examples must be
flagged by that control's detector and look-alikes must pass. Known false positives must be flagged and
known misses must pass, so the limits the registry states stay true.
"""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.control_registry import (CONTROLS, DOMAINS, REGISTRY_VERSION, control, maturity, registry_payload,
                                        repository_maturity)
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Stage
from guardoath.tenant_record import (CHECK_FIELDS, CHECK_IDS, EXTERNAL_CHECK_IDS, INPUT_ONLY_CHECK_IDS,
                                     TENANT_POLICY_VERSION, TenantRecord, demo_records)

ROOT = Path(__file__).resolve().parents[1]
PII_BASED = {"pii", "encoded_pii"}
LOCAL = [item for item in CONTROLS if not item.external_provider]
_CHECKERS = {}


def checkers_for(control_id):
    if control_id in PII_BASED:
        pytest.importorskip("tldextract")
        pytest.importorskip("phonenumbers")
    with_pii = control_id in PII_BASED
    if with_pii not in _CHECKERS:
        _CHECKERS[with_pii] = default_checkers(enable_pii=with_pii)
    return _CHECKERS[with_pii]


def only(control_id):
    """Demo user 1 with every control off except this one (flag), and the demo settings kept."""
    base = demo_records()[0]
    fields = {name: None for name in CHECK_FIELDS}
    fields["check_" + control_id] = "flag"
    return TenantRecord.model_validate({**base.model_dump(), **fields})


def result_of(control_id, example):
    record = only(control_id)
    engine = Engine([record.to_policy()], checkers_for(control_id))
    request = CheckRequest(application_id=record.tenant_id, policy_version=TENANT_POLICY_VERSION,
                           content=example.text, content_format=example.format)
    response = asyncio.run(engine.check(request, Stage(example.stage)))
    results = [r for r in response.checker_results if r.checker_id == control_id]
    assert len(results) == 1, [r.checker_id for r in response.checker_results]
    return results[0]


def cases(kind):
    return [pytest.param(item.control_id, example, id=f"{item.control_id}-{example.stage}-{n}")
            for item in LOCAL for n, example in enumerate(getattr(item, kind), 1)]


def test_every_tenant_record_check_is_one_control():
    assert sorted(item.control_id for item in CONTROLS) == sorted(CHECK_IDS)
    assert len(CONTROLS) == len(set(item.control_id for item in CONTROLS)) == 18
    for item in CONTROLS:
        assert item.field in CHECK_FIELDS and item.field in TenantRecord.model_fields
        assert control(item.control_id) is item


def test_actions_and_stages_are_the_tenant_record_rules():
    for item in CONTROLS:
        assert ("redact" in item.actions) == (item.control_id == "pii")
        assert item.stages == (("input",) if item.control_id in INPUT_ONLY_CHECK_IDS else ("input", "output"))
        assert item.external_provider == (item.control_id in EXTERNAL_CHECK_IDS)
        assert item.recommended_action in item.actions


def test_every_control_explains_why_to_turn_it_on_and_its_limits():
    for item in CONTROLS:
        assert item.name and item.outcome and item.use_case.startswith("Turn this on") and item.limitations
        assert item.positive_examples and item.look_alikes


def test_declared_maturity_matches_the_repository():
    for item in CONTROLS:
        declared, actual = maturity(item), repository_maturity(item, ROOT)
        assert {k: declared[k] for k in actual} == actual, item.control_id
        assert declared["integrated"] is True and declared["deployed"] is None


def test_planned_domains_have_no_controls_and_point_to_their_specification():
    for domain in DOMAINS:
        owned = [item for item in CONTROLS if item.domain == domain.domain_id]
        if domain.status == "planned":
            assert owned == [] and (ROOT / domain.reference).is_file(), domain.domain_id
        else:
            assert owned, domain.domain_id
    assert {d.domain_id for d in DOMAINS if d.status == "planned"} == {"human_safety", "security_events"}


@pytest.mark.parametrize("control_id,example", cases("positive_examples"))
def test_positive_examples_are_flagged_by_their_control(control_id, example):
    result = result_of(control_id, example)
    assert result.status == "flagged" and result.findings, (control_id, example.label, result.status, result.error_code)


@pytest.mark.parametrize("control_id,example", cases("look_alikes"))
def test_look_alikes_are_not_flagged(control_id, example):
    result = result_of(control_id, example)
    assert result.status == "passed" and not result.findings, (control_id, example.label, [f.rule_id for f in result.findings])


@pytest.mark.parametrize("control_id,example", cases("known_false_positives"))
def test_known_false_positives_really_are_flagged(control_id, example):
    assert result_of(control_id, example).status == "flagged", (control_id, example.label)


@pytest.mark.parametrize("control_id,example", cases("known_misses"))
def test_known_misses_really_pass(control_id, example):
    assert result_of(control_id, example).status == "passed", (control_id, example.label)


def test_outside_provider_controls_are_described_but_never_run_here():
    for item in CONTROLS:
        if item.external_provider:
            assert item.domain in {"content_harm", "manipulation"} and "outside" in item.name


def test_the_registry_endpoint_is_public_and_describes_every_control():
    client = TestClient(create_app(engine=demo_engine(), credentials={"test-key": "demo"}))
    response = client.get("/v1/controls")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300"
    body = response.json()
    assert body["registry_version"] == REGISTRY_VERSION and body["control_count"] == 18
    assert [d["domain_id"] for d in body["domains"]] == [d.domain_id for d in DOMAINS]
    listed = [c for d in body["domains"] for c in d["controls"]]
    assert sorted(c["control_id"] for c in listed) == sorted(CHECK_IDS)
    for item in listed:
        assert item["field"] == "input_check_" + item["control_id"]
        assert item["output_field"] == (None if item["control_id"] == "azure_prompt_shields"
                                        else "output_check_" + item["control_id"])
        assert item["guide"] == "/guide#check-" + item["control_id"]
        assert item["positive_examples"] and item["look_alikes"]
    assert "test-key" not in response.text and "gok_" not in response.text


def test_the_registry_reports_detector_versions_of_the_running_service():
    payload = registry_payload(default_checkers())
    versions = {c["control_id"]: c["detector"]["version"] for d in payload["domains"] for c in d["controls"]}
    assert versions["schema"] == "2"
    available = {c["control_id"]: c["maturity"]["available"] for d in payload["domains"] for c in d["controls"]}
    assert available["schema"] is True and available["content_safety"] is False
    assert registry_payload()["domains"][0]["controls"][0]["maturity"]["available"] is None


def test_the_registry_is_description_only():
    """The tenant record alone decides what runs: the registry's recommended action is not applied."""
    record = only("lexical_guards")
    assert record.input_check_lexical_guards == "flag" and control("lexical_guards").recommended_action == "flag"
    pii_record = TenantRecord.model_validate({**demo_records()[0].model_dump(), "input_check_pii": "flag", "output_check_pii": "flag"})
    assert pii_record.input_check_pii == "flag" and control("pii").recommended_action == "redact"
    rules = {r.checker_id: r.on_finding.value for r in pii_record.to_policy().rules}
    assert rules["pii"] == "flag"


def test_examples_are_plain_json_values():
    payload = registry_payload()
    json.dumps(payload, allow_nan=False)
    for domain in payload["domains"]:
        for item in domain["controls"]:
            for kind in ("positive_examples", "look_alikes", "known_false_positives", "known_misses"):
                for example in item[kind]:
                    assert set(example) == {"label", "stage", "format", "text"}
                    if example["format"] == "json":
                        json.loads(example["text"])
