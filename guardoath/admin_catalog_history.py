"""Owner-only projection of actual shipped catalog metadata release history.

The file is source-controlled, not an authenticated approval or deployment proof.
Verify every stored source fingerprint by reversing each release from the *current*
published registry. A malformed history is unavailable, never an empty history.
Nothing here modifies a customer tenant, detector, action or catalog source.
"""
from __future__ import annotations

from copy import deepcopy

from guardoath.admin_catalog_preview import _fingerprint
from guardoath.catalog_metadata import published_version, validate_release_history


class CatalogHistoryUnavailable(ValueError):
    """Shipped metadata history is inconsistent with the published registry."""


def verified_catalog_history(catalog: dict, history: dict) -> dict:
    """Verify the complete append-only fingerprint chain against one catalog view."""
    try:
        validate_release_history(history)
        if (not isinstance(catalog, dict)
                or not isinstance(catalog.get("registry_version"), str)
                or not isinstance(catalog.get("categories"), list)
                or catalog["registry_version"] != published_version(history)):
            raise ValueError("Published catalog version and history differ")

        snapshot = deepcopy(catalog)
        for entry in reversed(history["releases"]):
            if (snapshot["registry_version"] != entry["registry_version"]
                    or _fingerprint("guardoath-admin-catalog-source-v1", snapshot)
                    != entry["candidate_sha256"]):
                raise ValueError("Catalog release candidate fingerprint mismatch")
            matching = [
                rule for category in snapshot["categories"]
                if category["category_id"] == entry["category_id"]
                for check in category["checks"] if check["check_id"] == entry["check_id"]
                for rule in check["rules"] if rule["rule_id"] == entry["rule_id"]
            ]
            if len(matching) != 1:
                raise ValueError("Catalog release target not unique")
            for field, delta in entry["changes"].items():
                if matching[0].get(field) != delta["after"]:
                    raise ValueError("Catalog release description drift")
                matching[0][field] = delta["before"]
            snapshot["registry_version"] = str(int(entry["registry_version"]) - 1)
            if (_fingerprint("guardoath-admin-catalog-source-v1", snapshot)
                    != entry["baseline_sha256"]):
                raise ValueError("Catalog release baseline fingerprint mismatch")
        if snapshot["registry_version"] != history["base_registry_version"]:
            raise ValueError("Catalog base version drift")
        visible = history["releases"][-20:]
        return {
            "status": "verified_source_history",
            "source": "shipped source-controlled metadata history (not signed approval)",
            "registry_version": catalog["registry_version"],
            "release_count": len(history["releases"]),
            "has_older_releases": len(history["releases"]) > 20,
            "source_chain_verified": True,
            "live_acceptance_verified": None,
            "releases": [
                {"registry_version": item["registry_version"],
                 "category_id": item["category_id"], "check_id": item["check_id"],
                 "rule_id": item["rule_id"],
                 "review_id": item["review_id"], "draft_id": item["draft_id"],
                 "changed_fields": sorted(item["changes"])}
                for item in reversed(visible)],
        }
    except Exception as exc:
        raise CatalogHistoryUnavailable(
            "Shipped catalog metadata history cannot be verified") from exc
