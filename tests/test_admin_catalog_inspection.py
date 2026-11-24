"""Published owner inspection is bounded, audited and separate from tenant state."""
from copy import deepcopy
import json
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor
from guardoath.admin_catalog import read_catalog
from guardoath.admin_catalog_inspection import (
    CatalogInspectionConflict, CatalogInspectionNotFound, inspect_catalog,
)
from guardoath.admin_catalog_preview import _fingerprint
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.control_registry import registry_payload
from guardoath.rule_catalog import RULES, rule_payload
from guardoath.tenant_record import CHECK_IDS, SETTINGS_OF, TenantRecord

OWNER = "12345678-1234-1234-1234-123456789012"
HEADERS = {"X-GuardOath-Admin-Session": "owner"}
BASE = "/v1/admin/catalog/categories/privacy/checks/pii"


@pytest.fixture
def source():
    return registry_payload()


def selected(source):
    return source["domains"][0]["controls"][0]


def inspect(source, **changes):
    return inspect_catalog(lambda: source, **{
        "category_id": "privacy", "check_id": "pii",
        "expected_registry_version": source["registry_version"], **changes})


@pytest.mark.parametrize("check_id", CHECK_IDS)
def test_every_check_matches_shared_source_and_only_selected_tenant_schema(source, check_id):
    category = next(d for d in source["domains"] if any(c["control_id"] == check_id for c in d["controls"]))
    check = next(c for c in category["controls"] if c["control_id"] == check_id)
    before = deepcopy(source)
    result = inspect(source, category_id=category["domain_id"], check_id=check_id)
    assert result["category"]["description"] == category["description"]
    assert result["check"]["outcome"] == check["outcome"]
    assert result["check"]["supported_actions"] == check["actions"]
    assert result["check"]["stages"] == check["stages"]
    assert result["test_evidence"]["unit_tests"] == check["evidence"]["unit_tests"]
    assert result["test_evidence"]["evaluations"] == check["evidence"]["evaluations"]
    assert result["detector"] == check["detector"]
    assert result["test_evidence"]["ci_verified_for_source"] is False
    assert result["test_evidence"]["live_deployment_verified"] is None
    assert result["rule"] is None and result["read_only"] is True
    for group, examples in result["examples"].items():
        assert len(examples) == len(check[group])
        for item, raw in zip(examples, check[group]):
            assert json.loads(item["escaped_text"]) == raw["text"]
            assert {key: item[key] for key in ("label", "format", "stage")} == {
                key: raw[key] for key in ("label", "format", "stage")}
    fields = {f"input_check_{check_id}", f"output_check_{check_id}",
              *SETTINGS_OF.get(check_id, ()), "input_rule_actions", "output_rule_actions"}
    settings = result["settings"]
    assert set(settings["field_names"]) == set(settings["schema"]["properties"]) == fields
    full = TenantRecord.model_json_schema()
    assert settings["schema"]["properties"] == {name: full["properties"][name] for name in fields}
    assert not fields.intersection({"tenant_id", "key_hashes", "monthly_allowance", "payer_id", "billing_mode", "status"})
    reachable = set()
    def refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "$ref":
                    target = child.removeprefix("#/$defs/")
                    if target not in reachable:
                        reachable.add(target)
                        refs(settings["schema"]["$defs"][target])
                else:
                    refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)
    refs(settings["schema"]["properties"])
    assert reachable == set(settings["schema"].get("$defs", {}))
    assert settings["schema"].get("$defs", {}) == {key: full["$defs"][key] for key in reachable}
    assert source == before
    result["check"]["stages"].clear()
    settings["schema"]["properties"].clear()
    assert source == before


def test_rule_payload_preserves_real_example_prerequisites_and_existing_fingerprints(source):
    old = deepcopy(source)
    for domain in old["domains"]:
        for check in domain["controls"]:
            for rule in check["rules"]:
                info = RULES[rule["rule_id"]]
                assert rule["settings"] == list(info.settings)
                assert rule_payload(info)["needs_setting"] == info.needs_setting
                rule.pop("settings")
    baseline = read_catalog(lambda: old)
    assert read_catalog(lambda: source) == baseline
    assert _fingerprint("guardoath-admin-catalog-source-v1", baseline) == _fingerprint(
        "guardoath-admin-catalog-source-v1", read_catalog(lambda: source))


@pytest.mark.parametrize("rule_id", ["pii.email_address", "schema.invalid", "pii.encoded_date_of_birth"])
def test_exact_rule_example_and_dependencies_match_source(source, rule_id):
    # The encoded labeled rule has a prerequisite in a different check.
    info = RULES[rule_id]
    category = next(d for d in source["domains"] if any(c["control_id"] == info.control_id for c in d["controls"]))
    result = inspect(source, category_id=category["domain_id"], check_id=info.control_id, rule_id=rule_id)
    rule = result["rule"]
    assert rule["rule_id"] == info.rule_id
    assert json.loads(rule["example"]) == info.example
    assert json.loads(rule["look_alike"]) == info.look_alike
    assert rule["settings"] == list(info.settings)
    assert rule["needs_checks"] == list(info.needs_checks)
    assert rule["needs_setting"] == info.needs_setting
    assert rule["content_format"] == info.content_format


def test_inspection_calls_provider_once_and_does_not_read_tenant_values(source):
    calls = []
    def provider():
        calls.append(1)
        if len(calls) > 1:
            raise AssertionError("A second source snapshot must not be read")
        return source
    result = inspect_catalog(provider, category_id="privacy", check_id="pii",
                             expected_registry_version=source["registry_version"])
    assert result["registry_version"] == source["registry_version"] and calls == [1]


def test_provider_object_cannot_change_snapshot_after_identity_validation(source, monkeypatch):
    from guardoath import admin_catalog_inspection as module
    original = module._settings_schema
    expected = selected(source)["outcome"]
    def changing_schema(check_id):
        source["registry_version"] = "999999999"
        selected(source)["outcome"] = "Changed after capture"
        return original(check_id)
    version = source["registry_version"]
    monkeypatch.setattr(module, "_settings_schema", changing_schema)
    result = inspect(source)
    assert result["registry_version"] == version
    assert result["check"]["outcome"] == expected


def test_browser_schema_allowlist_matches_authoritative_tenant_mapping():
    from guardoath.admin_browser import _JS
    declaration = re.search(r"const catalogCheckSettings = (\{[^\n]+\});", _JS)
    assert declaration is not None
    assert json.loads(declaration[1]) == {key: list(value) for key, value in SETTINGS_OF.items()}


@pytest.mark.parametrize("changes", [
    {"category_id": "missing"}, {"check_id": "missing"}, {"category_id": "secrets"},
    {"rule_id": "secrets.private_key"}, {"rule_id": "missing.rule"},
    {"check_id": "../pii"}, {"category_id": None}, {"rule_id": ["pii.email_address"]},
])
def test_unknown_or_cross_category_target_never_returns_another_target(source, changes):
    with pytest.raises(CatalogInspectionNotFound):
        inspect(source, **changes)


def test_stale_version_is_a_conflict_not_a_silent_new_definition(source):
    with pytest.raises(CatalogInspectionConflict):
        inspect(source, expected_registry_version=str(int(source["registry_version"]) + 1))


@pytest.mark.parametrize("change", [
    lambda c: c.update(outcome={"private": "never-echo"}),
    lambda c: c.update(outcome="bad\u202edirection"),
    lambda c: c.update(recommended_action="unlock"),
    lambda c: c.update(positive_examples=None),
    lambda c: c.update(positive_examples=[{}] * 65),
    lambda c: c["positive_examples"][0].update(text={"private": "never-echo"}),
    lambda c: c["positive_examples"][0].update(text="x" * 16385),
    lambda c: c["positive_examples"][0].update(stage="admin"),
    lambda c: c["positive_examples"][0].update(format="html"),
    lambda c: c["evidence"].update(unit_tests=["tests/../secrets.py"]),
    lambda c: c["evidence"].update(unit_tests=["https://private.invalid/test.py"]),
    lambda c: c["evidence"].update(unit_tests=["tests/a.py", "tests/a.py"]),
    lambda c: c["evidence"].update(evaluations=["evals/test.json?secret"]),
    lambda c: c["detector"].update(implementation=["/private/provider.py"]),
    lambda c: c["detector"].update(version={"private": "never-echo"}),
    lambda c: c["rules"][0].update(needs_checks=["unknown"]),
    lambda c: c["rules"][0].update(settings=["key_hashes"]),
    lambda c: c["rules"][0].update(settings=["ledger"]),
    lambda c: c["rules"][0].update(needs_setting="stripe_customer_id"),
    lambda c: c["rules"][0].update(needs_setting="detect_secret_assignments"),
])
def test_malformed_selected_declarations_fail_closed(source, change):
    check = selected(source)
    change(check)
    with pytest.raises(ValueError):
        inspect(source, rule_id=check["rules"][0]["rule_id"])


def test_invisible_and_markup_examples_are_visible_escaped_literals(source):
    raw = '</pre><img src="https://private.invalid/" onerror="alert(1)">\n\u202e\x1b'
    selected(source)["positive_examples"][0]["text"] = raw
    value = inspect(source)["examples"]["positive_examples"][0]["escaped_text"]
    assert json.loads(value) == raw
    assert "\\u202e" in value and "\\u001b" in value and "\\n" in value
    assert "\u202e" not in value and "\x1b" not in value


def test_combined_inspection_size_has_a_hard_limit(source):
    selected(source)["positive_examples"] = [
        {"label": "Bounded individually", "stage": "input", "format": "text", "text": "x" * 16384}
    ] * 20
    with pytest.raises(ValueError, match="Oversized published inspection"):
        inspect(source)


def test_maximum_non_bmp_example_stays_within_shared_escaped_text_contract(source):
    selected(source)["positive_examples"][0]["text"] = "😀" * 16384
    value = inspect(source)["examples"]["positive_examples"][0]["escaped_text"]
    assert len(value) == 196610
    assert json.loads(value) == "😀" * 16384


def test_unselected_duplicate_source_id_is_not_hidden(source):
    source["domains"][-1]["controls"].append(deepcopy(selected(source)))
    with pytest.raises(ValueError):
        inspect(source)


@pytest.mark.parametrize("reference", ["https://private.invalid/schema", "#/properties/key_hashes", "#/$defs/Absent"])
def test_schema_refuses_unresolved_or_nonlocal_references(source, monkeypatch, reference):
    schema = TenantRecord.model_json_schema()
    schema["properties"]["input_check_pii"] = {"$ref": reference}
    monkeypatch.setattr(TenantRecord, "model_json_schema", lambda: schema)
    with pytest.raises(ValueError, match="schema reference"):
        inspect(source)


def make_client(provider, audit, verifier=None):
    app = FastAPI()
    app.include_router(create_admin_router(catalog_provider=provider,
        verify_cognito_admin=verifier or (lambda token: VerifiedAdmin(OWNER, frozenset({"owner"}), True)
                                         if token == "owner" else None),
        read_auditor=audit, require_persistent_read_audit=True))
    return TestClient(app)


def test_owner_api_persists_signed_read_before_inspection_and_never_changes_tenants(source, tmp_path):
    path = tmp_path / "catalog.sqlite3"
    db = AdminTestDynamo(path)
    saved = {"tenant_id": {"S": "usr-one"}, "record": {"S": '{"input_check_pii":null}'}}
    db.seed("tenant_table", saved)
    audit = AdminSensitiveReadAuditor(db, "audit_table", signing_key=ADMIN_BROWSER_AUDIT_KEY)
    reads = []
    def provider():
        assert len(db.audit_items()) == len(reads) + 1
        reads.append(1)
        return source
    with make_client(provider, audit) as client:
        for suffix in ("", "/rules/pii.email_address"):
            response = client.get(BASE + suffix, params={"expected_registry_version": source["registry_version"]}, headers=HEADERS)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            receipt = response.json()["read_audit_event_id"]
            reread = AdminTestDynamo(path)
            try:
                verified = AdminAuditReader(reread, "audit_table", verify_key=ADMIN_BROWSER_AUDIT_KEY).read(receipt)
                assert verified["integrity_status"] == "signed_verified"
                assert verified["change"] == {"field": "protected_read", "resource": "catalog.registry"}
                assert reread.get_item(TableName="tenant_table", Key={"tenant_id": {"S": "usr-one"}})["Item"] == saved
            finally:
                reread.close()
    assert reads == [1, 1] and db.transactions == 0
    db.close()


@pytest.mark.parametrize("principal", [None, VerifiedAdmin(OWNER, frozenset({"customer"}), True),
    VerifiedAdmin(OWNER, frozenset({"owner"}), False), VerifiedAdmin(OWNER, frozenset({"rule_publisher"}), True)])
def test_other_identities_cannot_read_or_audit_details(source, principal):
    calls = []
    class Audit:
        def log(self, **_): calls.append("audit"); return "a" * 64
    with make_client(lambda: calls.append("provider"), Audit(), lambda _: principal) as client:
        for suffix in ("", "/rules/pii.email_address"):
            result = client.get(BASE + suffix, params={"expected_registry_version": source["registry_version"]}, headers=HEADERS)
            assert result.status_code in (401, 403)
            assert client.get(BASE + suffix, params={"expected_registry_version": source["registry_version"]},
                              headers={"X-GuardOath-Key": "application-key"}).status_code == 401
    assert calls == []


def test_audit_outage_or_missing_configuration_blocks_provider(source):
    calls = []
    class FailedAudit:
        def log(self, **_): raise RuntimeError("private-audit-outage")
    for audit in (None, FailedAudit()):
        with make_client(lambda: calls.append(1), audit) as client:
            response = client.get(BASE, params={"expected_registry_version": source["registry_version"]}, headers=HEADERS)
            assert response.status_code == 503 and "private-audit-outage" not in response.text
    assert calls == []


def test_api_target_staleness_and_provider_faults_are_safe(source):
    class Audit:
        def log(self, **_): return "a" * 64
    with make_client(lambda: source, Audit()) as client:
        assert client.get(BASE, params={"expected_registry_version": "999999999"}, headers=HEADERS).status_code == 409
        assert client.get(BASE + "/rules/unknown.rule", params={"expected_registry_version": source["registry_version"]}, headers=HEADERS).status_code == 404
        selected(source)["outcome"] = {"private": "never-return-provider-body"}
        response = client.get(BASE, params={"expected_registry_version": source["registry_version"]}, headers=HEADERS)
        assert response.status_code == 503 and "never-return-provider-body" not in response.text
