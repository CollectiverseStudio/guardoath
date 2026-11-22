"""Source-shipped metadata reviews: reproducible forward/inverse, never an admin entitlement."""
from copy import deepcopy
from dataclasses import dataclass, replace
import pytest

from guardoath.admin_catalog import read_catalog
from guardoath.admin_catalog_preview import _fingerprint, preview_rule_description
from guardoath.catalog_metadata import (
    apply_rule_releases, published_version, validate_release_history)
from guardoath.catalog_source_release import candidate_release, SourceReleaseConflict

BASE = {"base_registry_version": "3", "releases": []}
SOURCE = {"registry_version": "3", "domains": [{
    "domain_id": "privacy", "name": "Privacy", "status": "active",
    "controls": [{
        "control_id": "pii", "name": "Personal identifiers",
        "stages": ["input", "output"], "actions": ["block", "flag"],
        "rules": [{"rule_id": "pii.email", "title": "Email rule",
                   "use_case": "Turn this on to protect personal emails"}],
    }],
}]}
PROPOSAL = {
    "expected_registry_version": "3", "proposed_registry_version": "4",
    "category_id": "privacy", "check_id": "pii", "rule_id": "pii.email",
    "title": "Safer email rule", "use_case": "Turn this on to protect personal emails",
}


@dataclass(frozen=True)
class FakeRule:
    rule_id: str
    control_id: str
    title: str
    use_case: str
    severity: str = "high"


def export_plan(source=SOURCE, proposal=PROPOSAL):
    preview = preview_rule_description(lambda: deepcopy(source), **proposal)
    base = read_catalog(lambda: deepcopy(source))
    candidate = deepcopy(base)
    target = candidate["categories"][0]["checks"][0]["rules"][0]
    for field, change in preview["changes"].items():
        target[field] = change["after"]
    candidate["registry_version"] = proposal["proposed_registry_version"]
    next_sha = _fingerprint("guardoath-admin-catalog-source-v1", candidate)
    changes = preview["changes"]
    return {
        "format": "guardoath.reviewed-catalog-metadata-source-plan.v1",
        "status": "review_only_not_published", "review_id": "a"*64,
        "draft_id": "b"*64, "registry_version": "3",
        "candidate_registry_version": "4",
        "reviewed_source_sha256": preview["baseline_sha256"],
        "reviewed_proposal_sha256": preview["proposal_sha256"],
        "candidate_metadata_sha256": next_sha,
        "rollback_source_sha256": preview["baseline_sha256"],
        "target": {"category_id": "privacy", "check_id": "pii", "rule_id": "pii.email"},
        "changed_fields": sorted(changes),
        "changed_metadata": deepcopy(changes),
        "forward": {
            "expected_registry_version": "3", "candidate_registry_version": "4",
            "expected_metadata_sha256": preview["baseline_sha256"],
            "candidate_metadata_sha256": next_sha, "changes": deepcopy(changes),
        },
        "rollback": {
            "expected_registry_version": "4", "candidate_registry_version": "3",
            "expected_metadata_sha256": next_sha,
            "candidate_metadata_sha256": preview["baseline_sha256"],
            "changes": {field: {"before": item["after"], "after": item["before"]}
                        for field, item in changes.items()},
        },
        "round_trip_verified": True, "hashes_are_not_signatures": True,
        "executable_source_committed": False, "published": False,
        "tenant_policies_changed": False, "detector_behavior_changed": False,
        "acceptance_tests_run": False,
    }


def test_current_shipped_history_is_inert_and_base_registry_unchanged():
    from guardoath.catalog_metadata import RELEASE_DATA
    assert published_version(RELEASE_DATA) == "3"
    assert RELEASE_DATA == BASE
    assert apply_rule_releases(
        {"pii.email": FakeRule("pii.email","pii","Email rule",
                               "Turn this on to protect personal emails")}, BASE
    )["pii.email"].title == "Email rule"


def test_owner_export_produces_true_shippable_metadata_change_without_mutating_input():
    original = deepcopy(SOURCE)
    export = export_plan()
    history = candidate_release(export, lambda: deepcopy(SOURCE), deepcopy(BASE))
    assert SOURCE == original and BASE == {"base_registry_version":"3","releases":[]}
    assert published_version(history) == "4"
    rule = FakeRule("pii.email", "pii", "Email rule",
                    "Turn this on to protect personal emails")
    output = apply_rule_releases({"pii.email":rule}, history)
    assert output["pii.email"].title == "Safer email rule"
    assert output["pii.email"].severity == "high"
    assert rule.title == "Email rule"
    assert history["releases"][0]["baseline_sha256"] == export["reviewed_source_sha256"]
    assert history["releases"][0]["candidate_sha256"] == export["candidate_metadata_sha256"]


@pytest.mark.parametrize("mutation", [
    lambda p: p.update({"published": True}),
    lambda p: p.update({"acceptance_tests_run": True}),
    lambda p: p.update({"registry_version":"4"}),
    lambda p: p.update({"reviewed_source_sha256":"f"*64}),
    lambda p: p.update({"reviewed_proposal_sha256":"f"*64}),
    lambda p: p.update({"candidate_metadata_sha256":"f"*64}),
    lambda p: p.update({"review_id":"not a review"}),
    lambda p: p.update({"unexpected":"extra"}),
    lambda p: p["target"].update({"rule_id":"pii.phone"}),
    lambda p: p["forward"]["changes"]["title"].update({"after":"Injected title"}),
    lambda p: p["rollback"]["changes"]["title"].update({"before":"Injected title"}),
    lambda p: p["changed_metadata"]["title"].update({"before":"fake"}),
])
def test_tampered_or_unreviewable_export_fails_closed(mutation):
    export = export_plan()
    mutation(export)
    with pytest.raises(SourceReleaseConflict):
        candidate_release(export,lambda: deepcopy(SOURCE),deepcopy(BASE))


@pytest.mark.parametrize("mutation", [
    lambda s: s.update({"registry_version":"4"}),
    lambda s: s["domains"][0]["controls"][0]["rules"][0].update({"title":"Changed"}),
])
def test_stale_source_does_not_create_versioned_release(mutation):
    source=deepcopy(SOURCE)
    mutation(source)
    with pytest.raises(SourceReleaseConflict):
        candidate_release(export_plan(), lambda: source, deepcopy(BASE))


def test_same_version_replay_and_unknown_rule_are_denied():
    history=candidate_release(export_plan(),lambda:deepcopy(SOURCE),deepcopy(BASE))
    with pytest.raises(SourceReleaseConflict):
        candidate_release(export_plan(),lambda:deepcopy(SOURCE),history)
    with pytest.raises(ValueError):
        apply_rule_releases({"other": FakeRule("other","pii","Email rule",
                                             "Turn this on to protect personal emails")},history)


def test_dual_field_metadata_release_and_strict_inverse():
    props={**PROPOSAL,"title":"Improved email protection",
           "use_case":"Turn this on to detect submitted email addresses"}
    export=export_plan(proposal=props)
    history=candidate_release(export,lambda:deepcopy(SOURCE),deepcopy(BASE))
    assert set(history["releases"][0]["changes"]) == {"title","use_case"}
    rule=FakeRule("pii.email","pii","Email rule","Turn this on to protect personal emails")
    assert apply_rule_releases({"pii.email":rule},history)["pii.email"].use_case == props["use_case"]


@pytest.mark.parametrize("change", [
    {"title": {"before":"Email rule", "after":"Safer email rule"},
     "severity": {"before":"high","after":"low"}},
    {"title": {"before":"Email rule", "after":"Email rule"}},
    {"title": {"before":"Email rule", "after":"A\u202ebad"}},
])
def test_release_history_rejects_policy_fields_noops_and_control_text(change):
    record=candidate_release(export_plan(),lambda:deepcopy(SOURCE),deepcopy(BASE))
    record["releases"][0]["changes"]=change
    with pytest.raises(ValueError):
        validate_release_history(record)
