"""Source-shipped metadata reviews: reproducible forward/inverse, never an admin entitlement."""
from copy import deepcopy
from dataclasses import dataclass, replace
import pytest

from guardoath.admin_catalog import read_catalog
from guardoath.admin_catalog_preview import _fingerprint, preview_rule_description
from guardoath.catalog_metadata import (
    apply_rule_releases, published_version, validate_release_history)
from guardoath.catalog_source_release import (
    candidate_release, candidate_rollback_release, SourceReleaseConflict)
from guardoath.admin_catalog_history import verified_catalog_history

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


def export_plan(source=SOURCE, proposal=PROPOSAL, *, review_id="a"*64, draft_id="b"*64):
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
        "status": "review_only_not_published", "review_id": review_id,
        "draft_id": draft_id, "registry_version": proposal["expected_registry_version"],
        "candidate_registry_version": proposal["proposed_registry_version"],
        "reviewed_source_sha256": preview["baseline_sha256"],
        "reviewed_proposal_sha256": preview["proposal_sha256"],
        "candidate_metadata_sha256": next_sha,
        "rollback_source_sha256": preview["baseline_sha256"],
        "target": {key: proposal[key] for key in ("category_id", "check_id", "rule_id")},
        "changed_fields": sorted(changes),
        "changed_metadata": deepcopy(changes),
        "forward": {
            "expected_registry_version": proposal["expected_registry_version"],
            "candidate_registry_version": proposal["proposed_registry_version"],
            "expected_metadata_sha256": preview["baseline_sha256"],
            "candidate_metadata_sha256": next_sha, "changes": deepcopy(changes),
        },
        "rollback": {
            "expected_registry_version": proposal["proposed_registry_version"],
            "candidate_registry_version": proposal["expected_registry_version"],
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


def _current_after_first_release():
    """The raw registry the next source build would publish, not an old fixture."""
    source = deepcopy(SOURCE)
    source["registry_version"] = "4"
    source["domains"][0]["controls"][0]["rules"][0]["title"] = "Safer email rule"
    history = candidate_release(export_plan(), lambda: deepcopy(SOURCE), deepcopy(BASE))
    return source, history


def _reviewed_inverse(source):
    return export_plan(
        source=source,
        proposal={**PROPOSAL, "expected_registry_version": "4",
                  "proposed_registry_version": "5", "title": "Email rule"},
        review_id="c"*64, draft_id="d"*64)


def test_fresh_reviewed_inverse_appends_not_erases_version_and_preserves_tenant_neutrality():
    source, history = _current_after_first_release()
    before_source, before_history = deepcopy(source), deepcopy(history)
    inverse = _reviewed_inverse(source)
    assert inverse["forward"]["expected_registry_version"] == "4"
    assert inverse["forward"]["candidate_registry_version"] == "5"
    assert inverse["rollback"]["expected_registry_version"] == "5"
    assert inverse["rollback"]["candidate_registry_version"] == "4"
    rollback = candidate_rollback_release(inverse, lambda: deepcopy(source), history)
    assert source == before_source and history == before_history
    assert published_version(rollback) == "5"
    assert len(rollback["releases"]) == 2
    first, second = rollback["releases"]
    assert second["baseline_sha256"] == first["candidate_sha256"]
    assert second["candidate_sha256"] == inverse["candidate_metadata_sha256"]
    assert second["changes"]["title"] == {"before": "Safer email rule", "after": "Email rule"}
    original = FakeRule("pii.email", "pii", "Email rule",
                        "Turn this on to protect personal emails")
    restored = apply_rule_releases({"pii.email": original}, rollback)["pii.email"]
    assert restored == original
    published = read_catalog(lambda: deepcopy(source))
    published["registry_version"] = "5"
    published["categories"][0]["checks"][0]["rules"][0]["title"] = "Email rule"
    inspected = verified_catalog_history(published, rollback)
    assert inspected["source_chain_verified"] is True
    assert inspected["release_count"] == 2
    assert [entry["registry_version"] for entry in inspected["releases"]] == ["5", "4"]


def test_rollback_requires_existing_publication_exact_inverse_and_fresh_review():
    source, history = _current_after_first_release()
    inverse = _reviewed_inverse(source)
    with pytest.raises(SourceReleaseConflict):
        candidate_rollback_release(export_plan(), lambda: deepcopy(SOURCE), BASE)
    for mutated in (
        {**inverse, "review_id": "a"*64},
        {**inverse, "draft_id": "b"*64},
        {**inverse, "reviewed_source_sha256": "f"*64},
        {**inverse, "target": {**inverse["target"], "rule_id": "pii.phone"}},
        {**inverse, "changed_fields": []},
        {**inverse, "forward": {**inverse["forward"], "changes": {
            "title": {"before": "Safer email rule", "after": "Another email rule"}}}},
    ):
        with pytest.raises(SourceReleaseConflict):
            candidate_rollback_release(mutated, lambda: deepcopy(source), history)
    # A new source reviewer is not allowed to reuse a previously shipped
    # review or draft, even when the rest of the payload is self-consistent.
    replay_review = export_plan(source=source,
        proposal={**PROPOSAL, "expected_registry_version": "4",
                  "proposed_registry_version": "5", "title": "Email rule"},
        review_id="a"*64, draft_id="d"*64)
    with pytest.raises(SourceReleaseConflict):
        candidate_release(replay_review, lambda: deepcopy(source), history)
    replay_draft = _reviewed_inverse(source)
    replay_draft["draft_id"] = "b"*64
    with pytest.raises(SourceReleaseConflict):
        candidate_release(replay_draft, lambda: deepcopy(source), history)
    assert len(history["releases"]) == 1


def test_forged_source_history_reusing_review_or_draft_id_is_rejected():
    source, history = _current_after_first_release()
    inverse = _reviewed_inverse(source)
    rollback = candidate_rollback_release(inverse, lambda: deepcopy(source), history)
    for field in ("review_id", "draft_id"):
        damaged = deepcopy(rollback)
        damaged["releases"][1][field] = damaged["releases"][0][field]
        with pytest.raises(ValueError):
            validate_release_history(damaged)


def test_rollback_does_not_approve_unrelated_new_description_or_partial_inverse():
    source, history = _current_after_first_release()
    arbitrary = export_plan(
        source=source,
        proposal={**PROPOSAL, "expected_registry_version": "4",
                  "proposed_registry_version": "5", "title": "Another email rule"},
        review_id="c"*64, draft_id="d"*64)
    assert arbitrary["forward"]["expected_registry_version"] == "4"
    assert arbitrary["forward"]["candidate_registry_version"] == "5"
    # An ordinary new metadata release remains possible, but it cannot be
    # misrepresented as a rollback of the most recent published release.
    assert published_version(candidate_release(
        arbitrary, lambda: deepcopy(source), history)) == "5"
    with pytest.raises(SourceReleaseConflict):
        candidate_rollback_release(arbitrary, lambda: deepcopy(source), history)
    dual = {**PROPOSAL, "title": "Safer email rule",
            "use_case": "Turn this on to detect submitted email addresses"}
    first = candidate_release(export_plan(proposal=dual),
                              lambda: deepcopy(SOURCE), deepcopy(BASE))
    dual_source = deepcopy(SOURCE)
    dual_source["registry_version"] = "4"
    rule = dual_source["domains"][0]["controls"][0]["rules"][0]
    rule["title"] = dual["title"]
    rule["use_case"] = dual["use_case"]
    # Intentionally revert only the title; the original release also changed
    # use_case, so this MUST NOT count as a complete rollback.
    partial = export_plan(
        source=dual_source,
        proposal={**PROPOSAL, "expected_registry_version": "4",
                  "proposed_registry_version": "5", "title": "Email rule",
                  "use_case": dual["use_case"]},
        review_id="c"*64, draft_id="d"*64)
    assert set(partial["changed_fields"]) == {"title"}
    assert set(first["releases"][0]["changes"]) == {"title", "use_case"}
    with pytest.raises(SourceReleaseConflict):
        candidate_rollback_release(partial, lambda: deepcopy(dual_source), first)


def _offline_release_cli(monkeypatch, tmp_path):
    """Use the real source tool with an isolated tracked-history file."""
    import json
    import guardoath.catalog_source_release as release

    source = tmp_path / "catalog_metadata_releases.json"
    source.write_text(json.dumps(BASE, indent=2) + "\n", encoding="utf-8")
    plan = tmp_path / "reviewed-plan.json"
    plan.write_text(json.dumps(export_plan()), encoding="utf-8")
    monkeypatch.setattr(release, "FILE", source)
    monkeypatch.setattr(release, "published_catalog", lambda: deepcopy(SOURCE))
    lock = source.with_name("." + source.name + ".write.lock")
    return release, source, plan, lock


def test_offline_catalog_release_cli_dry_run_and_exclusive_atomic_write(monkeypatch, tmp_path):
    import json

    release, source, plan, lock = _offline_release_cli(monkeypatch, tmp_path)
    original = source.read_bytes()
    args = ["--plan", str(plan), "--confirm-registry-version", "4"]
    release.main(args)
    assert source.read_bytes() == original
    assert not lock.exists()
    release.main([*args, "--write"])
    written = json.loads(source.read_text(encoding="utf-8"))
    assert written == candidate_release(export_plan(), lambda: deepcopy(SOURCE), deepcopy(BASE))
    assert written["releases"][0]["registry_version"] == "4"
    assert not lock.exists()
    assert not list(tmp_path.glob(".catalog-release-*"))


def test_offline_catalog_release_cli_refuses_concurrent_writer_without_edit(monkeypatch, tmp_path):
    release, source, plan, lock = _offline_release_cli(monkeypatch, tmp_path)
    original = source.read_bytes()
    lock.write_text("another-writer", encoding="ascii")
    with pytest.raises(SourceReleaseConflict, match="lock unavailable"):
        release.main(["--plan", str(plan), "--write", "--confirm-registry-version", "4"])
    assert source.read_bytes() == original
    assert lock.read_text(encoding="ascii") == "another-writer"
    assert not list(tmp_path.glob(".catalog-release-*"))


def test_offline_catalog_release_cli_rejects_out_of_band_change_without_overwrite(monkeypatch, tmp_path):
    release, source, plan, lock = _offline_release_cli(monkeypatch, tmp_path)
    initial = source.read_bytes()
    competing_bytes = b'{  "base_registry_version": "3", "releases": [] }\n'
    assert competing_bytes != initial

    def concurrent_source():
        source.write_bytes(competing_bytes)
        return deepcopy(SOURCE)

    monkeypatch.setattr(release, "published_catalog", concurrent_source)
    with pytest.raises(SourceReleaseConflict, match="changed during release"):
        release.main(["--plan", str(plan), "--write", "--confirm-registry-version", "4"])
    assert source.read_bytes() == competing_bytes
    assert not lock.exists()
    assert not list(tmp_path.glob(".catalog-release-*"))


def test_offline_catalog_release_cli_cleans_lock_and_temp_on_publish_failure(monkeypatch, tmp_path):
    release, source, plan, lock = _offline_release_cli(monkeypatch, tmp_path)
    original = source.read_bytes()

    def refused_replace(*args):
        raise OSError("injected source publish failure")

    monkeypatch.setattr(release.os, "replace", refused_replace)
    with pytest.raises(OSError, match="injected"):
        release.main(["--plan", str(plan), "--write", "--confirm-registry-version", "4"])
    assert source.read_bytes() == original
    assert not lock.exists()
    assert not list(tmp_path.glob(".catalog-release-*"))


def test_offline_catalog_release_cli_bounds_actual_plan_read_before_source_write(monkeypatch, tmp_path):
    release, source, plan, lock = _offline_release_cli(monkeypatch, tmp_path)
    original = source.read_bytes()
    plan.write_bytes(b"x" * 65537)
    with pytest.raises(SystemExit):
        release.main(["--plan", str(plan), "--write", "--confirm-registry-version", "4"])
    assert source.read_bytes() == original
    assert not lock.exists()
