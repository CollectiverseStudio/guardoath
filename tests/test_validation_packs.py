"""Offline tests for trusted validation-pack resolution; no detector claims."""
import pytest
from valinator.validation_packs import Pack, PackRegistry, PackSelectionError


def registry():
    return PackRegistry({
        "logistics": (
            Pack("LOG", "1", ("timestamps", "cross_field")),
            Pack("AGENT", "1", ("tool_calls", "timestamps")),
        ),
        "health": (Pack("HLTH", "1", ("pii",)),),
    })


def test_core_is_mandatory_and_overlap_runs_once():
    selected = registry().resolve("logistics", ("LOG", "AGENT"))
    assert PackRegistry.checker_union(("schema", "timestamps"), selected) == (
        "schema", "timestamps", "cross_field", "tool_calls"
    )


def test_no_optional_packs_preserves_core():
    assert PackRegistry.checker_union(("schema",), registry().resolve("logistics")) == ("schema",)


@pytest.mark.parametrize("codes", [
    ("HLTH",), ("UNKNOWN",), ("log",), ("CORE",), ("LOG", "LOG"),
    ("LOG;AGENT",), ("LOG ",), tuple(["LOG"] * 13),
])
def test_unknown_or_malformed_codes_are_rejected(codes):
    with pytest.raises(PackSelectionError):
        registry().resolve("logistics", codes)


def test_cross_tenant_isolation():
    with pytest.raises(PackSelectionError):
        registry().resolve("health", ("LOG",))


def test_bad_registry_is_rejected():
    with pytest.raises(ValueError):
        PackRegistry({"a": (Pack("CORE", "1", ("schema",)),)})
    with pytest.raises(ValueError):
        PackRegistry({"a": (Pack("LOG", "1", ("schema",)), Pack("LOG", "2", ("pii",)))})


def test_request_contract_accepts_multiple_codes_and_rejects_malformed():
    from pydantic import ValidationError
    from valinator.models import CheckRequest
    base = dict(application_id="demo", policy_version="v1", content="hello")
    assert CheckRequest(**base, validation_codes=["LOG", "AGENT"]).validation_codes == ("LOG", "AGENT")
    with pytest.raises(ValidationError):
        CheckRequest(**base, validation_codes=["log"])
    with pytest.raises(ValidationError):
        CheckRequest(**base, validation_codes=["LOG"] * 13)


def test_engine_selects_optional_rules_and_retains_core():
    import asyncio
    from valinator.engine import Engine
    from valinator.models import CheckRequest, Policy, Rule, Stage
    from valinator.checkers import default_checkers

    policy = Policy(application_id="demo", version="v1", rules=(
        Rule(checker_id="text_security"),
        Rule(checker_id="schema", required=False),
    ))
    registry = PackRegistry({"demo": (Pack("LOG", "1", ("schema",)),)})
    engine = Engine([policy], default_checkers(), pack_registry=registry)
    def run(codes):
        return asyncio.run(engine.check(CheckRequest(
            application_id="demo", policy_version="v1", content="hello",
            validation_codes=codes,
        ), Stage.INPUT))
    assert [r.checker_id for r in run(()).checker_results] == ["text_security"]
    assert [r.checker_id for r in run(("LOG",)).checker_results] == ["text_security", "schema"]
    with pytest.raises(PackSelectionError):
        run(("AGENT",))


def test_api_rejects_unconfigured_pack_without_implicit_fallback():
    from fastapi.testclient import TestClient
    from valinator.app import create_app
    from valinator.engine import Engine
    from valinator.models import Policy, Rule
    from valinator.checkers import default_checkers
    engine = Engine([Policy(application_id="demo", version="v1", rules=(
        Rule(checker_id="text_security"),
    ))], default_checkers())
    client = TestClient(create_app(engine=engine, credentials={"test-key": "demo"}))
    result = client.post("/v1/checks/input",
        headers={"Authorization": "Bearer test-key"},
        json={"application_id": "demo", "policy_version": "v1",
              "content": "hello", "validation_codes": ["LOG"]})
    assert result.status_code == 422
    assert "hello" not in result.text


def test_api_activates_pack_and_preserves_baseline():
    from fastapi.testclient import TestClient
    from valinator.app import create_app
    from valinator.engine import Engine
    from valinator.models import Policy, Rule
    from valinator.checkers import default_checkers
    registry = PackRegistry({"demo": (Pack("LOG", "1", ("schema",)),)})
    policy = Policy(application_id="demo", version="v1", rules=(
        Rule(checker_id="text_security"),
        Rule(checker_id="schema", required=False),
    ))
    client = TestClient(create_app(
        engine=Engine([policy], default_checkers(), pack_registry=registry),
        credentials={"test-key": "demo"},
    ))
    headers = {"Authorization": "Bearer test-key"}
    payload = {"application_id": "demo", "policy_version": "v1", "content": "hello"}
    baseline = client.post("/v1/checks/input", headers=headers, json=payload)
    selected = client.post("/v1/checks/input", headers=headers,
                           json={**payload, "validation_codes": ["LOG"]})
    assert baseline.status_code == selected.status_code == 200
    assert baseline.json()["applied_validation_packs"] == ["CORE"]
    assert selected.json()["applied_validation_packs"] == ["CORE", "LOG@1"]
    assert [r["checker_id"] for r in baseline.json()["checker_results"]] == ["text_security"]
    assert [r["checker_id"] for r in selected.json()["checker_results"]] == ["text_security", "schema"]
    unauthorized = client.post("/v1/checks/input", headers=headers,
                               json={**payload, "validation_codes": ["AGENT"]})
    assert unauthorized.status_code == 422
    assert "hello" not in unauthorized.text


def test_load_published_registry_and_reject_invalid_files(tmp_path):
    import json
    from valinator.validation_packs import load_pack_registry
    target = tmp_path / "packs.json"
    target.write_text(json.dumps({"format_version": "1", "applications": {
        "demo": [{"code": "LOG", "version": "1", "checker_ids": ["timestamps"]}]
    }}))
    assert load_pack_registry(target).resolve("demo", ("LOG",))[0].version == "1"
    target.write_text(json.dumps({"format_version": "1", "applications": {
        "demo": [{"code": "LOG", "version": "1", "checker_ids": ["timestamps"],
                  "untrusted_override": True}]
    }}))
    with pytest.raises(ValueError, match="Unable to load"):
        load_pack_registry(target)
    target.write_bytes(b"x" * 262145)
    with pytest.raises(ValueError, match="Unable to load"):
        load_pack_registry(target)


def test_startup_requires_policy_file_when_registry_is_configured(monkeypatch):
    from valinator.app import create_app
    monkeypatch.setenv("VALINATOR_PACK_FILE", "missing.json")
    monkeypatch.delenv("VALINATOR_POLICY_FILE", raising=False)
    with pytest.raises(ValueError, match="requires a policy file"):
        create_app()
