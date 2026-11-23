"""Turn a reviewed owner UI export into a proposed, source-shipped metadata release.

This is an offline SOURCE action, never a /v1/admin write or a live publisher.
An unsigned UI export cannot authorize deployment or bypass independent review,
exact-source CI, a non-forced commit, rollback and 15 live acceptance checks.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile

from guardoath.admin_catalog import read_catalog, published_catalog
from guardoath.admin_catalog_preview import _fingerprint
from guardoath.catalog_metadata import (
    FILE, _safe_text, published_version, validate_release_history,
)

FORMAT = "guardoath.reviewed-catalog-metadata-source-plan.v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = frozenset({"title", "use_case"})
_EXPORT = frozenset({
    "format", "status", "review_id", "draft_id",
    "registry_version", "candidate_registry_version",
    "reviewed_source_sha256", "reviewed_proposal_sha256",
    "candidate_metadata_sha256", "rollback_source_sha256",
    "target", "changed_fields", "changed_metadata",
    "forward", "rollback", "round_trip_verified",
    "hashes_are_not_signatures", "executable_source_committed",
    "published", "tenant_policies_changed", "detector_behavior_changed",
    "acceptance_tests_run",
})


class SourceReleaseConflict(ValueError):
    """Review export is stale, forged, ambiguous or not source-reproducible."""


def _reject_duplicate_keys(pairs):
    """Fail closed on ambiguous JSON, including duplicates nested in a review."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceReleaseConflict("Duplicate source release JSON key")
        result[key] = value
    return result


def _reject_nonfinite(value):
    raise SourceReleaseConflict("Nonfinite source release JSON number")


def _strict_json(raw):
    """Python's default JSON decoder silently replaces duplicate object keys."""
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicate_keys,
                          parse_constant=_reject_nonfinite)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise SourceReleaseConflict("Invalid source release JSON") from exc


def candidate_release(export: dict, provider, history: dict) -> dict:
    """Recompute the ENTIRE source fingerprint; no export field is trusted."""
    try:
        validate_release_history(history)
        if (not isinstance(export, dict) or set(export) != _EXPORT
                or export["format"] != FORMAT
                or export["status"] != "review_only_not_published"
                or export["round_trip_verified"] is not True
                or export["hashes_are_not_signatures"] is not True
                or any(export[k] is not False for k in (
                    "executable_source_committed", "published",
                    "tenant_policies_changed", "detector_behavior_changed",
                    "acceptance_tests_run"))
                or any(not isinstance(export[k], str) or not _SHA.fullmatch(export[k])
                       for k in ("review_id", "draft_id", "reviewed_source_sha256",
                                 "reviewed_proposal_sha256", "candidate_metadata_sha256",
                                 "rollback_source_sha256"))):
            raise SourceReleaseConflict("Unverified owner source export")
        # Use exactly one independently read and copied source snapshot.
        catalog = read_catalog(lambda: deepcopy(provider()))
        current = catalog["registry_version"]
        if (current != published_version(history)
                or export["registry_version"] != current
                or export["candidate_registry_version"] != str(int(current) + 1)):
            raise SourceReleaseConflict("Source registry version is stale")
        baseline = _fingerprint("guardoath-admin-catalog-source-v1", catalog)
        if (export["reviewed_source_sha256"] != baseline
                or export["rollback_source_sha256"] != baseline):
            raise SourceReleaseConflict("Source catalog differs from reviewed snapshot")
        target = export["target"]
        if (not isinstance(target, dict) or
                set(target) != {"category_id", "check_id", "rule_id"} or
                not all(isinstance(v, str) for v in target.values())):
            raise SourceReleaseConflict("Reviewed target unavailable")
        rules = [
            rule for category in catalog["categories"]
            if category["category_id"] == target["category_id"]
            for check in category["checks"]
            if check["check_id"] == target["check_id"]
            for rule in check["rules"] if rule["rule_id"] == target["rule_id"]
        ]
        if len(rules) != 1:
            raise SourceReleaseConflict("Reviewed source target ambiguous")
        fields = export["changed_fields"]
        changed = export["changed_metadata"]
        forward = export["forward"]
        rollback = export["rollback"]
        if (not isinstance(fields, list) or not fields
                or len(fields) > 2 or len(set(fields)) != len(fields)
                or any(field not in _FIELDS for field in fields)
                or not isinstance(changed, dict)
                or set(changed) != set(fields)
                or not isinstance(forward, dict) or not isinstance(rollback, dict)
                or set(forward) != {"expected_registry_version", "candidate_registry_version",
                                   "expected_metadata_sha256", "candidate_metadata_sha256", "changes"}
                or set(rollback) != set(forward)
                or forward["expected_registry_version"] != current
                or forward["candidate_registry_version"] != export["candidate_registry_version"]
                or rollback["expected_registry_version"] != export["candidate_registry_version"]
                or rollback["candidate_registry_version"] != current
                or forward["expected_metadata_sha256"] != baseline
                or rollback["candidate_metadata_sha256"] != baseline
                or forward["candidate_metadata_sha256"] != export["candidate_metadata_sha256"]
                or rollback["expected_metadata_sha256"] != export["candidate_metadata_sha256"]
                or not isinstance(forward["changes"], dict)
                or not isinstance(rollback["changes"], dict)
                or set(forward["changes"]) != set(fields)
                or set(rollback["changes"]) != set(fields)):
            raise SourceReleaseConflict("Source and rollback plan disagree")
        changes = {}
        for field in fields:
            change = forward["changes"][field]
            inverse = rollback["changes"][field]
            if (not isinstance(change, dict) or set(change) != {"before", "after"}
                    or not isinstance(inverse, dict) or set(inverse) != {"before", "after"}
                    or change != changed[field]
                    or inverse != {"before": change["after"], "after": change["before"]}
                    or change["before"] != rules[0][field]
                    or not _safe_text(change["before"], 120 if field == "title" else 500)
                    or not _safe_text(change["after"], 120 if field == "title" else 500)
                    or (field == "use_case" and
                        not change["after"].startswith("Turn this on"))):
                raise SourceReleaseConflict("Source description would not reproduce")
            changes[field] = change
        proposal = {
            "source_catalog_sha256": baseline,
            "expected_registry_version": current,
            "candidate_registry_version": export["candidate_registry_version"],
            **target, "changes": changes,
        }
        if _fingerprint("guardoath-admin-catalog-proposal-v1", proposal) != export["reviewed_proposal_sha256"]:
            raise SourceReleaseConflict("Export does not match reviewed proposal")
        candidate = deepcopy(catalog)
        for category in candidate["categories"]:
            if category["category_id"] != target["category_id"]:
                continue
            for check in category["checks"]:
                if check["check_id"] != target["check_id"]:
                    continue
                for rule in check["rules"]:
                    if rule["rule_id"] == target["rule_id"]:
                        for field, delta in changes.items():
                            rule[field] = delta["after"]
        candidate["registry_version"] = export["candidate_registry_version"]
        if _fingerprint("guardoath-admin-catalog-source-v1", candidate) != export["candidate_metadata_sha256"]:
            raise SourceReleaseConflict("Export candidate fingerprint mismatch")
        # Reverse it and prove full source projection, not just local field parity.
        for category in candidate["categories"]:
            for check in category["checks"]:
                for rule in check["rules"]:
                    if (category["category_id"] == target["category_id"]
                            and check["check_id"] == target["check_id"]
                            and rule["rule_id"] == target["rule_id"]):
                        for field, delta in changes.items():
                            rule[field] = delta["before"]
        candidate["registry_version"] = current
        if candidate != catalog:
            raise SourceReleaseConflict("Rollback is not exact")
        if any(entry["review_id"] == export["review_id"] or
               entry["draft_id"] == export["draft_id"]
               for entry in history["releases"]):
            raise SourceReleaseConflict("Review or draft already used by a published release")
        record = {
            "registry_version": export["candidate_registry_version"],
            **target, "review_id": export["review_id"],
            "draft_id": export["draft_id"],
            "baseline_sha256": baseline,
            "candidate_sha256": export["candidate_metadata_sha256"],
            "changes": deepcopy(changes),
        }
        next_history = deepcopy(history)
        next_history["releases"].append(record)
        return validate_release_history(next_history)
    except SourceReleaseConflict:
        raise
    except Exception as exc:
        raise SourceReleaseConflict("Catalog source release unavailable") from exc


def candidate_rollback_release(export: dict, provider, history: dict) -> dict:
    """Append a NEW reviewed version reversing exactly the latest metadata release.

    Rollback is intentionally another source-controlled forward publication, not
    deletion of history or an in-place mutation of a deployed registry. A fresh
    owner-reviewed inverse export is required; this never approves deployment.
    """
    try:
        validate_release_history(history)
        if not history["releases"]:
            raise SourceReleaseConflict("No published metadata release to reverse")
        last = history["releases"][-1]
        if (not isinstance(export, dict)
                or export.get("reviewed_source_sha256") != last["candidate_sha256"]
                or export.get("target") != {
                    "category_id": last["category_id"],
                    "check_id": last["check_id"],
                    "rule_id": last["rule_id"],
                }
                or not isinstance(export.get("forward"), dict)):
            raise SourceReleaseConflict("Reversal is not bound to latest published source")
        expected_inverse = {
            field: {"before": delta["after"], "after": delta["before"]}
            for field, delta in last["changes"].items()
        }
        if (export["forward"].get("changes") != expected_inverse
                or export.get("changed_metadata") != expected_inverse
                or export.get("changed_fields") != sorted(expected_inverse)):
            raise SourceReleaseConflict("Reversal must restore every field of latest release")
        return candidate_release(export, provider, history)
    except SourceReleaseConflict:
        raise
    except Exception as exc:
        raise SourceReleaseConflict("Reviewed source reversal unavailable") from exc


@contextmanager
def _source_write_lock(path: Path):
    """Cooperative same-directory exclusive writer reservation, never stolen.

    Non-cooperating source edits are additionally detected by comparing the
    initial bytes immediately before publication. An abandoned lock requires
    operator inspection; silently removing it could race another writer.
    """
    if path.is_symlink() or not path.is_file():
        raise SourceReleaseConflict("Tracked catalog source file unavailable")
    lock = path.with_name("." + path.name + ".write.lock")
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise SourceReleaseConflict("Catalog source writer lock unavailable") from exc
    try:
        os.close(fd)
        yield
    finally:
        # Do not auto-recover a pre-existing lock; it may belong to a live writer.
        lock.unlink()


def _source_snapshot(path: Path) -> tuple[bytes, dict]:
    if path.is_symlink():
        raise SourceReleaseConflict("Tracked catalog source file unavailable")
    # Read at most the accepted source length plus one byte. A corrupt or
    # adversarially enlarged tracked file must not be loaded into memory.
    with path.open("rb") as source_file:
        raw = source_file.read(262145)
    if len(raw) > 262144:
        raise SourceReleaseConflict("Oversized catalog source history")
    try:
        history = validate_release_history(_strict_json(raw))
    except (ValueError, UnicodeError, TypeError) as exc:
        raise SourceReleaseConflict("Published source history unavailable") from exc
    return raw, history


def main(argv=None):
    parser = argparse.ArgumentParser(description="Propose a source-only GuardOath catalog metadata release")
    parser.add_argument("--plan", type=Path, required=True,
                        help="Reviewed owner Catalog JSON export (not a signature)")
    parser.add_argument("--write", action="store_true",
                        help="Compare-and-swap the tracked source release file under an exclusive lock; never deploy")
    parser.add_argument("--revert-last", action="store_true",
                        help="Require a fresh reviewed export that exactly reverses the latest shipped metadata release")
    parser.add_argument("--confirm-registry-version", default="",
                        help="Required version to prevent accidental source modifications")
    args = parser.parse_args(argv)
    # Bound the actual read, not just a racing stat result; never load an
    # arbitrarily large untrusted source plan into the operator process.
    with args.plan.open("rb") as plan_file:
        plan_bytes = plan_file.read(65537)
    if len(plan_bytes) > 65536:
        parser.error("Oversized review export")
    export = _strict_json(plan_bytes)
    prepare = candidate_rollback_release if args.revert_last else candidate_release
    with (_source_write_lock(FILE) if args.write else nullcontext()):
        original_bytes, history = _source_snapshot(FILE)
        updated = prepare(export, published_catalog, history)
        version = published_version(updated)
        if args.write:
            if args.confirm_registry_version != version:
                parser.error("Explicit proposed registry version required for source write")
            encoded = json.dumps(updated, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
            temp = None
            try:
                with tempfile.NamedTemporaryFile(
                        mode="w", encoding="utf-8", dir=FILE.parent,
                        prefix=".catalog-release-", delete=False) as f:
                    temp = Path(f.name)
                    f.write(encoded)
                    f.flush()
                    os.fsync(f.fileno())
                # Refuse an out-of-band source edit rather than overwriting
                # another writer's review, even when its version stayed equal.
                # Recheck the complete old snapshot with one bounded read:
                # a concurrent append must fail without allocating its size.
                with FILE.open("rb") as current_file:
                    current_bytes = current_file.read(len(original_bytes) + 1)
                if current_bytes != original_bytes or FILE.is_symlink():
                    raise SourceReleaseConflict("Catalog source changed during release; no write")
                os.replace(temp, FILE)
            finally:
                if temp is not None and temp.exists():
                    temp.unlink()
    print("Catalog source proposal version", version,
          "- source file", str(FILE),
          "- written" if args.write else "- DRY RUN; no file changed",
          "- NOT approved or deployed")

if __name__ == "__main__":
    main()
