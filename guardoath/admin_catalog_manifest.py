"""Source-bound, reviewed system-catalog metadata release manifest (NOT a publisher).

A manifest is an immutable *calculation* from the current published registry and
an independently verified persisted draft/review. It is not an executable catalog,
a source commit, a test attestation or a tenant policy update.
"""
from __future__ import annotations

from copy import deepcopy

from guardoath.admin_catalog import read_catalog
from guardoath.admin_catalog_preview import (
    _fingerprint, CatalogPreviewConflict, preview_rule_description,
)


class CatalogManifestConflict(Exception):
    """A saved review no longer matches the published registry."""


class CatalogManifestUnavailable(Exception):
    """A signed review, draft or source snapshot could not be verified."""


def reviewed_metadata_manifest(provider, *, draft: dict, review: dict) -> dict:
    """Calculate exactly one next-version metadata diff from one source snapshot.

    The authenticated caller first reads independently HMAC-verified, owner-bound
    review and draft records. This routine is deliberately read-only and never
    imports any tenant mutator, detector or registry writer.
    """
    if (not callable(provider) or not isinstance(draft, dict)
            or not isinstance(review, dict)
            or review.get("status") != "review_recorded"
            or review.get("published") is not False
            or draft.get("status") != "draft_saved"
            or draft.get("published") is not False
            or draft.get("detector_behavior_changed") is not False
            or draft.get("tenant_policies_changed") is not False
            or review.get("draft_id") != draft.get("draft_id")
            or review.get("baseline_sha256") != draft.get("baseline_sha256")
            or review.get("proposal_sha256") != draft.get("proposal_sha256")):
        raise CatalogManifestUnavailable("Verified catalog review and draft required")
    try:
        # One read, deep-copied to prevent a second provider snapshot from
        # silently changing while the fingerprint and candidate are calculated.
        raw = deepcopy(provider())
        current = read_catalog(lambda: raw)
        refreshed = preview_rule_description(
            lambda: raw,
            expected_registry_version=draft["registry_version"],
            proposed_registry_version=draft["candidate_registry_version"],
            category_id=draft["category_id"], check_id=draft["check_id"],
            rule_id=draft["rule_id"], title=draft["title"],
            use_case=draft["use_case"])
    except (CatalogPreviewConflict, ValueError) as exc:
        raise CatalogManifestConflict("Reviewed catalog source is stale") from exc
    except Exception as exc:
        raise CatalogManifestUnavailable("Catalog source unavailable") from exc

    if (refreshed["baseline_sha256"] != review["baseline_sha256"]
            or refreshed["proposal_sha256"] != review["proposal_sha256"]
            or refreshed["changes"] != draft.get("changes")
            or refreshed["registry_version"] != draft["registry_version"]
            or refreshed["candidate_registry_version"] != draft["candidate_registry_version"]):
        raise CatalogManifestConflict("Reviewed source or proposal changed")
    candidate = deepcopy(current)
    targets = [
        rule for category in candidate["categories"]
        if category["category_id"] == draft["category_id"]
        for check in category["checks"]
        if check["check_id"] == draft["check_id"]
        for rule in check["rules"] if rule["rule_id"] == draft["rule_id"]
    ]
    if len(targets) != 1 or not refreshed["changes"]:
        raise CatalogManifestConflict("Reviewed catalog target changed")
    for field, delta in refreshed["changes"].items():
        if (field not in ("title", "use_case") or not isinstance(delta, dict)
                or targets[0].get(field) != delta.get("before")
                or delta.get("after") != draft[field]):
            raise CatalogManifestUnavailable("Unexpected metadata-only change")
        targets[0][field] = delta["after"]
    candidate["registry_version"] = draft["candidate_registry_version"]
    original_sha = _fingerprint("guardoath-admin-catalog-source-v1", current)
    if original_sha != review["baseline_sha256"]:
        raise CatalogManifestConflict("Published baseline changed")
    candidate_sha = _fingerprint("guardoath-admin-catalog-source-v1", candidate)
    if candidate_sha == original_sha:
        raise CatalogManifestUnavailable("Candidate metadata did not change")
    return {
        "status": "metadata_manifest_preview_only",
        "source": "one independently verified published registry snapshot",
        "review_id": review["review_id"], "draft_id": draft["draft_id"],
        "reviewed_source_sha256": original_sha,
        "reviewed_proposal_sha256": review["proposal_sha256"],
        "candidate_metadata_sha256": candidate_sha,
        "registry_version": draft["registry_version"],
        "candidate_registry_version": draft["candidate_registry_version"],
        "rollback_source_sha256": original_sha,
        "rollback_registry_version": draft["registry_version"],
        "category_id": draft["category_id"], "check_id": draft["check_id"],
        "rule_id": draft["rule_id"],
        "changed_fields": sorted(refreshed["changes"]),
        "changed_metadata": refreshed["changes"],
        "category_count": current["total_categories"],
        "check_count": current["total_checks"],
        "rule_count": current["total_rules"],
        "published": False, "ready_to_publish": False,
        "source_current": True, "tenant_policies_changed": False,
        "detector_behavior_changed": False, "acceptance_tests_run": False,
        "release_kind": "review_only_projection_not_executable_source",
        "requires": "versioned source generation, benign/adversarial/timeout tests, exact-SHA CI, deployment, rollback and 15 live acceptance",
    }
