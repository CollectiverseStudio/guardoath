"""Owner-only read-time preview of versioned catalog description edits.

No draft is persisted and no category, detector, tenant policy, or engine code
is published. The authenticated API is responsible for verifying owner + MFA.
"""
from __future__ import annotations

import hashlib
import json
import re

from guardoath.admin_catalog import CatalogProvider, CatalogRegistryConflict, read_catalog


_VERSION = re.compile(r"^[1-9][0-9]{0,8}$")
_CONTROL = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
_RULE = re.compile(r"^[A-Za-z0-9_.:-]{3,100}$")


class CatalogPreviewConflict(Exception):
    """The draft references a stale registry version or unknown system rule."""


class CatalogPreviewUnavailable(Exception):
    """The authoritative published registry could not be verified."""


def _fingerprint(domain: str, payload: dict) -> str:
    """Stable metadata fingerprint, not a signature, approval or publish receipt."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(domain.encode("ascii") + b"\x00" + canonical).hexdigest()


def _copy(value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid catalog description")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("Invalid catalog description")
    result = value.strip()
    if not 3 <= len(result) <= max_length:
        raise ValueError("Invalid catalog description")
    return result


def preview_rule_description(provider: CatalogProvider, *, expected_registry_version: str,
                             proposed_registry_version: str, category_id: str, check_id: str,
                             rule_id: str, title: str, use_case: str) -> dict:
    """Verify next-version metadata proposal against the actual published registry.

    Cannot propose detector, severity, actions, stage coverage, IDs, tenant
    settings or host reference trust changes. This is not a publish operation.
    """
    if (not isinstance(expected_registry_version, str) or
            not _VERSION.fullmatch(expected_registry_version) or
            not isinstance(proposed_registry_version, str) or
            not _VERSION.fullmatch(proposed_registry_version) or
            not isinstance(category_id, str) or not _CONTROL.fullmatch(category_id) or
            not isinstance(check_id, str) or not _CONTROL.fullmatch(check_id) or
            not isinstance(rule_id, str) or not _RULE.fullmatch(rule_id)):
        raise ValueError("Invalid catalog proposal identity or version")
    new_title, new_use_case = _copy(title, 120), _copy(use_case, 500)
    try:
        catalog = read_catalog(provider)
    except CatalogRegistryConflict as exc:
        # Preserve explicit stale/ambiguous-catalog (409) semantics without
        # allowing a duplicate ID to be previewed or converted into an edit.
        raise CatalogPreviewConflict("Published catalog identifiers ambiguous") from exc
    except Exception as exc:
        raise CatalogPreviewUnavailable("Published catalog unavailable") from exc
    current_version = catalog.get("registry_version")
    if not isinstance(current_version, str) or not _VERSION.fullmatch(current_version):
        raise CatalogPreviewUnavailable("Published registry version unavailable")
    if (expected_registry_version != current_version or
            int(proposed_registry_version) != int(current_version) + 1):
        raise CatalogPreviewConflict("Registry version changed; refresh before preview")
    candidates = [rule for category in catalog["categories"] if category["category_id"] == category_id
                  for check in category["checks"] if check["check_id"] == check_id
                  for rule in check["rules"] if rule["rule_id"] == rule_id]
    if len(candidates) != 1:
        raise CatalogPreviewConflict("Published catalog rule unavailable or ambiguous")
    rule = candidates[0]
    previous_title, previous_use_case = rule["title"], rule["use_case"]
    changes = {}
    if new_title != previous_title:
        changes["title"] = {"before": previous_title, "after": new_title}
    if new_use_case != previous_use_case:
        changes["use_case"] = {"before": previous_use_case, "after": new_use_case}
    if not changes:
        raise ValueError("No description change to preview")
    # Bind the proposal to the entire published projection, not just a version
    # label. A same-version change also invalidates the review fingerprint.
    # These digests are NOT signatures, approvals, or publish receipts.
    try:
        baseline_sha = _fingerprint("guardoath-admin-catalog-source-v1", catalog)
        proposal_sha = _fingerprint("guardoath-admin-catalog-proposal-v1", {
            "source_catalog_sha256": baseline_sha,
            "expected_registry_version": current_version,
            "candidate_registry_version": proposed_registry_version,
            "category_id": category_id, "check_id": check_id, "rule_id": rule_id,
            "changes": changes,
        })
    except (TypeError, ValueError, OverflowError) as exc:
        raise CatalogPreviewUnavailable("Published catalog fingerprint unavailable") from exc
    return {"status": "preview_only", "published": False, "persisted": False,
            "source": "server-side published GuardOath registry",
            "registry_version": current_version,
            "proposed_title": new_title, "proposed_use_case": new_use_case,
            "baseline_sha256": baseline_sha, "proposal_sha256": proposal_sha,
            "fingerprint_kind": "review_only_untrusted_sha256_not_signature",
            "candidate_registry_version": proposed_registry_version,
            "category_id": category_id, "check_id": check_id, "rule_id": rule_id,
            "changes": changes, "changed_fields": sorted(changes),
            "tenant_policies_changed": False, "detector_behavior_changed": False,
            "requires": "review, tests, versioned source commit, release verification and live acceptance"}
