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
        "demo": {"core_checker_ids": ["text_security"], "packs": [{"code": "LOG", "version": "1", "checker_ids": ["timestamps"]}]}
    }}))
    assert load_pack_registry(target).resolve("demo", ("LOG",))[0].version == "1"
    target.write_text(json.dumps({"format_version": "1", "applications": {
        "demo": {"core_checker_ids": ["text_security"], "packs": [{"code": "LOG", "version": "1", "checker_ids": ["timestamps"],
                  "untrusted_override": True}]}
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


def test_explicit_core_cannot_be_made_optional():
    from valinator.engine import Engine
    from valinator.models import Policy, Rule
    from valinator.checkers import default_checkers
    with pytest.raises(ValueError, match="CORE checker cannot be optional"):
        PackRegistry({"demo": (Pack("LOG", "1", ("text_security",)),)},
                     core={"demo": ("text_security",)})
    registry = PackRegistry({"demo": (Pack("LOG", "1", ("schema",)),)},
                            core={"demo": ("text_security",)})
    with pytest.raises(ValueError, match="partition policy checks"):
        Engine([Policy(application_id="demo", version="v1",
                       rules=(Rule(checker_id="text_security"),))],
               default_checkers(), pack_registry=registry)


def test_real_demo_bundle_core_log_and_agent():
    import asyncio
    from pathlib import Path
    from valinator.config import load_policies
    from valinator.validation_packs import load_pack_registry
    from valinator.checkers import default_checkers
    from valinator.engine import Engine
    from valinator.models import CheckRequest, Stage
    root = Path(__file__).resolve().parents[1]
    engine = Engine(
        load_policies(root / "policies/validation-packs-demo.json"),
        default_checkers(),
        pack_registry=load_pack_registry(root / "policies/validation-packs.example.json"),
    )
    def run(content, codes=(), fmt="text"):
        return asyncio.run(engine.check(CheckRequest(
            application_id="demo", policy_version="valisentry-packs-v1",
            content=content, content_format=fmt, validation_codes=codes,
        ), Stage.INPUT))
    baseline = run("Hello, world")
    assert [r.checker_id for r in baseline.checker_results] == ["text_security"]
    assert baseline.applied_validation_packs == ("CORE",)
    good_log = run('{"samples":[{"timestamp":"2026-10-28T10:00:00Z","value":1},'
                   '{"timestamp":"2026-10-28T10:01:00Z","value":2}]}', ("LOG",), "json")
    assert good_log.applied_validation_packs == ("CORE", "LOG@1")
    assert next(r for r in good_log.checker_results if r.checker_id == "timestamps").status == "passed"
    bad_log = run('{"samples":[{"timestamp":"2026-10-28T10:01:00Z","value":1},'
                  '{"timestamp":"2026-10-28T10:00:00Z","value":2}]}', ("LOG",), "json")
    assert bad_log.recommended_action == "block"
    good_agent = run('{"tool_calls":[{"name":"lookup_shipment",'
                     '"arguments":{"shipment_id":"SHP-1234"}}]}', ("AGENT",), "json")
    assert next(r for r in good_agent.checker_results if r.checker_id == "tool_calls").status == "passed"
    bad_agent = run('{"tool_calls":[{"name":"delete_shipment",'
                    '"arguments":{"shipment_id":"SHP-1234"}}]}', ("AGENT",), "json")
    assert bad_agent.recommended_action == "block"
    assert any(f.rule_id == "actions.unknown_tool" for f in bad_agent.findings)


def test_registry_rejects_duplicate_json_keys_and_core_optional_overlap(tmp_path):
    from valinator.validation_packs import load_pack_registry
    path = tmp_path / "registry.json"
    path.write_text('{"format_version":"1","format_version":"1","applications":{}}')
    with pytest.raises(ValueError, match="Unable to load"):
        load_pack_registry(path)
    path.write_text('{"format_version":"1","applications":{"demo":{'
                    '"core_checker_ids":["text_security"],"packs":[{'
                    '"code":"LOG","version":"1","checker_ids":["text_security"]}]}}}')
    with pytest.raises(ValueError, match="Unable to load"):
        load_pack_registry(path)


def test_authenticated_pack_discovery_is_tenant_scoped():
    from fastapi.testclient import TestClient
    from valinator.app import create_app
    from valinator.engine import Engine
    from valinator.models import Policy, Rule
    from valinator.checkers import default_checkers
    reg = PackRegistry({
        "demo": (Pack("LOG", "1", ("schema",)),),
        "other": (Pack("HLTH", "2", ("pii",)),),
    })
    engine = Engine([
        Policy(application_id="demo", version="v1", rules=(
            Rule(checker_id="text_security"), Rule(checker_id="schema", required=False))),
        Policy(application_id="other", version="v1", rules=(
            Rule(checker_id="text_security"), Rule(checker_id="pii", required=False))),
    ], default_checkers(), pack_registry=reg)
    client = TestClient(create_app(engine=engine, credentials={
        "demo-key": "demo", "other-key": "other"}))
    assert client.get("/v1/validation-packs").status_code == 401
    response = client.get("/v1/validation-packs", headers={
        "Authorization": "Bearer demo-key"})
    assert response.status_code == 200
    assert response.json() == {"application_id": "demo", "mandatory": "CORE",
                               "available_packs": [{"code": "LOG", "version": "1"}]}
    assert "HLTH" not in response.text
    other = client.get("/v1/validation-packs", headers={
        "Authorization": "Bearer other-key"})
    assert other.json()["available_packs"] == [{"code": "HLTH", "version": "2"}]


def test_registry_snapshots_mutable_caller_inputs():
    source = ["schema"]
    pack = Pack("LOG", "1", source)
    published = {"demo": [pack]}
    registry = PackRegistry(published, core={"demo": ("text_security",)})
    source.append("tool_calls")
    published["demo"].append(Pack("AGENT", "1", ("tool_calls",)))
    assert registry.resolve("demo", ("LOG",))[0].checker_ids == ("schema",)
    with pytest.raises(PackSelectionError):
        registry.resolve("demo", ("AGENT",))


def test_required_incompatible_packs_are_not_reported_clean():
    import asyncio
    from pathlib import Path
    from valinator.config import load_policies
    from valinator.validation_packs import load_pack_registry
    from valinator.checkers import default_checkers
    from valinator.engine import Engine
    from valinator.models import CheckRequest, Stage
    root = Path(__file__).resolve().parents[1]
    engine = Engine(load_policies(root / "policies/validation-packs-demo.json"),
                    default_checkers(),
                    pack_registry=load_pack_registry(root / "policies/validation-packs.example.json"))
    result = asyncio.run(engine.check(CheckRequest(
        application_id="demo", policy_version="valisentry-packs-v1",
        content='{"samples":[{"timestamp":"2026-10-28T10:00:00Z","value":1},'
                '{"timestamp":"2026-10-28T10:01:00Z","value":2}]}',
        content_format="json", validation_codes=("LOG", "AGENT"),
    ), Stage.INPUT))
    assert result.applied_validation_packs == ("CORE", "LOG@1", "AGENT@1")
    assert result.evaluation_status == "incomplete"
    assert result.recommended_action == "block"
    assert result.effective_action == "block"
    assert any(r.checker_id == "tool_calls" and r.status != "passed" for r in result.checker_results)
