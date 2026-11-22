"""Admin catalog evidence and completeness are published-source only, fail closed."""
from copy import deepcopy
import pytest
from guardoath.admin_catalog import read_catalog


def source():
    return {"registry_version": "3", "domains": [
        {"domain_id": "privacy", "name": "Privacy", "controls": [
            {"control_id": "pii", "name": "Identifiers", "rule_count": 1,
             "stages": ["input", "output"], "actions": ["block", "redact"],
             "use_case": "Protect user identifiers",
             "limitations": "Does not identify every name",
             "evidence": {"unit_tests": ["tests/hidden.py", "tests/other.py"],
                          "evaluations": ["evals/private.json"]},
             "positive_examples": [{"text": "SENSITIVE_EXAMPLE"}],
             "detector": {"implementation": ["private/path.py"]},
             "rules": [{"rule_id": "pii.email", "title": "Email", "use_case": "Protect email"}]}]},
        {"domain_id": "planned", "name": "Planned", "controls": []}]}


def test_evidence_is_counts_only_and_never_current_ci_or_live_attestation():
    report = read_catalog(source)
    item = report["categories"][0]["checks"][0]
    assert item["supported_actions"] == ["block", "redact"]
    assert item["use_case"] == "Protect user identifiers"
    assert item["limitations"] == "Does not identify every name"
    assert item["test_evidence"] == {
        "unit_test_files_declared": 2, "evaluation_files_declared": 1,
        "ci_verified_for_source": False, "live_deployment_verified": None,
        "basis": "registry declarations, not executed test results"}
    exposed = str(report)
    for secret in ("hidden.py", "private.json", "private/path", "SENSITIVE_EXAMPLE"):
        assert secret not in exposed
    assert read_catalog(source, domain_id="privacy")["total_rules"] == 1


@pytest.mark.parametrize("mutate", [
    lambda p: p["domains"].append(deepcopy(p["domains"][0])),
    lambda p: p["domains"][0]["controls"].append(deepcopy(p["domains"][0]["controls"][0])),
    lambda p: p["domains"][0]["controls"][0]["rules"].append(
        deepcopy(p["domains"][0]["controls"][0]["rules"][0])),
    lambda p: p["domains"][0]["controls"][0]["rules"].append({"title": "missing id"}),
    lambda p: p["domains"][0]["controls"][0]["rules"].append("not a rule"),
    lambda p: p["domains"][0]["controls"][0].update({"rule_count": True}),
    lambda p: p["domains"][0]["controls"][0].update({"stages": ["input", "input"]}),
    lambda p: p["domains"][0]["controls"][0].update({"actions": ["grant_paid"]}),
    lambda p: p["domains"][0]["controls"][0].update({"evidence": {"unit_tests": "private/raw/path"}}),
    lambda p: p["domains"][1].update({"domain_id": "privacy"}),
    lambda p: p.update({"registry_version": True}),
    lambda p: p.update({"registry_version": "not_versioned"}),
    lambda p: p["domains"][0].update({"name": {"private": "provider internal"}}),
    lambda p: p["domains"][0].update({"status": "\x00forged"}),
    lambda p: p["domains"][0].update({"name": "Trusted\u202ealarmed"}),
    lambda p: p["domains"][0]["controls"][0].update({"use_case": "Safe\u200binvisible"}),
    lambda p: p["domains"][0]["controls"][0]["rules"][0].update({"title": "Rule\u2066isolate"}),
    lambda p: p["domains"][0]["controls"][0]["rules"][0].update({"use_case": "line\u0085break"}),
    lambda p: p["domains"][0]["controls"][0].update({"name": ["private"]}),
    lambda p: p["domains"][0]["controls"][0].update({"use_case": "x" * 4097}),
    lambda p: p["domains"][0]["controls"][0].update({"stages": {}}),
    lambda p: p["domains"][0]["controls"][0].update({"stages": None}),
    lambda p: p["domains"][0]["controls"][0].update({"stages": [True]}),
    lambda p: p["domains"][0]["controls"][0].update({"actions": False}),
    lambda p: p["domains"][0]["controls"][0].update({"actions": [[]]}),
    lambda p: p["domains"][0]["controls"][0]["rules"][0].update({"title": {"secret": 1}}),
    lambda p: p["domains"][0]["controls"][0]["rules"][0].update({"use_case": "x" * 4097}),
])
def test_corrupt_source_is_rejected_even_when_category_view_is_filtered(mutate):
    p = source()
    mutate(p)
    with pytest.raises(ValueError):
        read_catalog(lambda: p, domain_id="planned")


def test_real_registry_shapes_and_declared_counts_are_compatible():
    from guardoath.control_registry import registry_payload
    report = read_catalog(registry_payload)
    assert report["total_checks"] >= 19
    assert report["total_rules"] >= 100
    for category in report["categories"]:
        for check in category["checks"]:
            assert check["test_evidence"]["ci_verified_for_source"] is False
            assert check["test_evidence"]["live_deployment_verified"] is None
            assert check["rule_count"] == len(check["rules"])


def test_catalog_projection_does_not_share_mutable_eligibility_with_registry():
    raw = source()
    report = read_catalog(lambda: raw)
    published = raw["domains"][0]["controls"][0]
    item = report["categories"][0]["checks"][0]
    item["stages"].append("output-other")
    item["supported_actions"].append("grant_paid")
    assert published["stages"] == ["input", "output"]
    assert published["actions"] == ["block", "redact"]


def test_catalog_rejects_oversized_unfiltered_provider_data():
    raw = source()
    raw["domains"] = [dict(domain_id="item" + str(i), controls=[])
                      for i in range(129)]
    with pytest.raises(ValueError):
        read_catalog(lambda: raw, domain_id="planned")


def test_admin_api_fails_closed_without_leaking_invalid_catalog_metadata():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from guardoath.admin_console import VerifiedAdmin, create_admin_router
    raw = source()
    raw["domains"][0]["controls"][0]["rules"][0]["title"] = {"secret": "NEVER_LOG_THIS"}
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(
            "12345678-1234-1234-1234-123456789012", frozenset({"owner"}), True)
        if token == "owner" else None,
        catalog_provider=lambda: raw))
    client = TestClient(app)
    url = "/v1/admin/catalog?category_id=planned"
    assert client.get(url).status_code == 401
    result = client.get(url, headers={"X-GuardOath-Admin-Session": "owner"})
    assert result.status_code == 503
    assert "NEVER_LOG_THIS" not in result.text
