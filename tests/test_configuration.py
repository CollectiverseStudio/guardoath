import json

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import Policy, Rule


def test_example_file_loads():
    policies = load_policies("policies/support.json")
    assert policies[0].version == "support-v1"
    assert Engine(policies, default_checkers()).has_policy("demo", "support-v1")


@pytest.mark.parametrize("raw", ["not JSON", "{}", '{"format_version":"2","policies":[]}',
                                      '{"format_version":"1","policies":[]}'])
def test_invalid_configuration_is_sanitized(tmp_path, raw):
    path = tmp_path / "private-policy.json"
    path.write_text(raw)
    with pytest.raises(ValueError, match="^Unable to load policy configuration$"):
        load_policies(path)


def test_policy_file_size_is_bounded(tmp_path):
    path = tmp_path / "too-big.json"
    path.write_bytes(b" " * 1_048_577)
    with pytest.raises(ValueError, match="Unable to load"):
        load_policies(path)


@pytest.mark.parametrize("reference", ["https://example.com/schema.json", "file:///etc/passwd", "other.json"])
def test_remote_schema_references_are_rejected(reference):
    policy = Policy(application_id="demo", version="v1", rules=(), input_schema={"$ref": reference})
    with pytest.raises(ValueError, match="Only local schema references"):
        Engine([policy], default_checkers())


def test_local_references_are_allowed():
    schema = {"$defs": {"value": {"type": "integer"}}, "$ref": "#/$defs/value"}
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="schema"),), input_schema=schema)
    assert Engine([policy], default_checkers()).has_policy("demo", "v1")


def test_environment_loads_policy_file(monkeypatch, tmp_path):
    path = tmp_path / "policies.json"
    path.write_text(json.dumps({"format_version": "1", "policies": [{
        "application_id": "demo", "version": "custom-v1", "rules": [{"checker_id": "schema"}],
        "input_schema": {"type": "integer"}}]}))
    monkeypatch.setenv("GUARDOATH_POLICY_FILE", str(path))
    client = TestClient(create_app(credentials={"test-key": "demo"}))
    response = client.post("/v1/checks/input", headers={"Authorization": "Bearer test-key"}, json={
        "application_id": "demo", "policy_version": "custom-v1", "content": "42", "content_format": "json"})
    assert response.status_code == 200
    assert response.json()["evaluation_status"] == "complete"


@pytest.fixture
def client():
    return TestClient(create_app(credentials={"test-key": "demo"}))


def check(client, content, **extra):
    return client.post("/v1/checks/input", headers={"Authorization": "Bearer test-key"}, json={
        "application_id": "demo", "policy_version": "support-v1", "content": content,
        "content_format": "json", **extra})


@pytest.mark.parametrize("content", ["not json", '{"message": "first", "message": "second"}',
                                   '{"message": NaN}', '{"message": Infinity}', '42'])
def test_bad_canonical_content_is_flagged(client, content):
    result = check(client, content).json()
    assert result["recommended_action"] == "block"
    assert result["findings"][0]["category"] == "structure"


def test_separate_payload_cannot_bypass_content_validation(client):
    response = check(client, '{"wrong": "value"}', payload={"message": "apparently valid"})
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid check request"}


def test_text_skips_optional_schema_but_reports_it(client):
    result = check(client, "hello", content_format="text").json()
    assert result["checker_results"][0]["status"] == "skipped"
    assert result["evaluation_status"] == "complete"
    assert result["simulated"] is True


def test_required_schema_cannot_be_bypassed_with_text():
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="schema"),),
                    input_schema={"type": "integer"})
    api = TestClient(create_app(engine=Engine([policy], default_checkers()), credentials={"test-key": "demo"}))
    response = api.post("/v1/checks/input", headers={"Authorization": "Bearer test-key"}, json={
        "application_id": "demo", "policy_version": "v1", "content": "hello"})
    assert response.json()["evaluation_status"] == "incomplete"


def test_disabled_pii_is_incomplete_not_a_pass():
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="pii"),))
    api = TestClient(create_app(engine=Engine([policy], default_checkers()), credentials={"test-key": "demo"}))
    response = api.post("/v1/checks/input", headers={"Authorization": "Bearer test-key"}, json={
        "application_id": "demo", "policy_version": "v1", "content": "hello"})
    assert response.json()["evaluation_status"] == "incomplete"
    assert response.json()["checker_results"][0]["status"] == "unsupported"


def test_invalid_pii_setting_fails_startup(monkeypatch):
    monkeypatch.setenv("GUARDOATH_ENABLE_PII", "yes")
    with pytest.raises(ValueError, match="must be 0 or 1"):
        create_app(credentials={})


def _requirement_closure(name, extras=()):
    """Distribution names required by `name` (with `extras`), transitively, by metadata."""
    import re
    from importlib.metadata import PackageNotFoundError, distribution
    seen, pending = set(), [(name, tuple(extras))]
    while pending:
        current, wanted = pending.pop()
        key = re.sub(r"[-_.]+", "-", current).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            requirements = distribution(current).requires or []
        except PackageNotFoundError:
            continue
        for requirement in requirements:
            requirement_name = re.match(r"[A-Za-z0-9._-]+", requirement).group(0)
            extra = re.search(r"extra\s*==\s*[\"']([^\"']+)", requirement)
            if extra is None or extra.group(1) in wanted:
                pending.append((requirement_name, ()))
    return seen


def test_pii_checker_loads_only_its_declared_dependencies():
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    import re
    import subprocess
    import sys
    from importlib.metadata import packages_distributions
    code = ("import sys; before = set(sys.modules); import guardoath.pii; "
            "checker = guardoath.pii.build_pii_checker(); print(checker.checker_version); "
            "print(' '.join(sorted({m.split('.')[0] for m in set(sys.modules) - before})))")
    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.splitlines()
    assert output[0] == "privacy-patterns-2.2.366/guardoath-patterns-4"
    loaded = set(output[1].split()) - set(sys.stdlib_module_names) - {"guardoath"}
    allowed = _requirement_closure("guardoath", ("pii",))
    owners = packages_distributions()
    outside = {module for module in loaded
               if not {re.sub(r"[-_.]+", "-", owner).lower() for owner in owners.get(module, [])} & allowed}
    assert loaded and not outside
