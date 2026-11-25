"""Explicit owner authority to prepare one source-shipped catalog change.

This service records a separately confirmed request from independently verified
draft/review evidence. It never runs Git, changes runtime source, dispatches a
worker, changes tenant policy or reports deployment from a prepared candidate.
"""
from __future__ import annotations

from copy import deepcopy
import re
import time

from guardoath.admin_catalog_drafts import DraftUnavailable
from guardoath.admin_catalog_manifest import (
    CatalogManifestConflict, CatalogManifestUnavailable, reviewed_metadata_manifest)
from guardoath.admin_catalog_publication_state import (
    DynamoCatalogPublicationState, EMPTY_CONTROL_HASH,
    PublicationConflict, PublicationUnavailable, document_hash)
from guardoath.catalog_metadata import _safe_text
from guardoath.catalog_publication_source import PublicationSourceUnavailable
from guardoath.catalog_source_release import (
    FORMAT, SourceReleaseConflict, candidate_release, candidate_rollback_release)

_HEX = re.compile(r"^[a-f0-9]{64}$")
_SHA = re.compile(r"^[a-f0-9]{40}$")
_OWNER = re.compile(r"^[0-9a-fA-F-]{20,80}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
_OPERATIONS = {"publish_metadata", "reverse_latest"}


def source_plan_from_manifest(manifest: dict) -> dict:
    """Allowlist the existing compiler export, without granting authority to it."""
    try:
        if (manifest["status"] != "metadata_manifest_preview_only"
                or manifest["source_current"] is not True
                or manifest["published"] is not False
                or manifest["ready_to_publish"] is not False):
            raise ValueError()
        patch = manifest["reversible_patch"]
        plan = {name: deepcopy(manifest[name]) for name in (
            "review_id", "draft_id", "registry_version", "candidate_registry_version",
            "reviewed_source_sha256", "reviewed_proposal_sha256", "candidate_metadata_sha256",
            "rollback_source_sha256", "changed_fields", "changed_metadata")}
        return {**plan, "format": FORMAT, "status": "review_only_not_published",
            "target": deepcopy(patch["target"]), "forward": deepcopy(patch["forward"]),
            "rollback": deepcopy(patch["rollback"]), "round_trip_verified": True,
            "hashes_are_not_signatures": True, "executable_source_committed": False,
            "published": False, "tenant_policies_changed": False,
            "detector_behavior_changed": False, "acceptance_tests_run": False}
    except (KeyError, TypeError, ValueError) as exc:
        raise PublicationUnavailable("Verified metadata manifest unavailable") from exc


class AdminCatalogPublications:
    def __init__(self, *, state: DynamoCatalogPublicationState, drafts,
                 catalog_provider, history_provider, source_provider, clock=None):
        if (not isinstance(state, DynamoCatalogPublicationState)
                or getattr(drafts, "ddb", None) is not state.client
                or getattr(drafts, "table", None) != state.table_name
                or not all(callable(value) for value in
                           (catalog_provider, history_provider, source_provider))):
            raise ValueError("Bound signed catalog state and source providers required")
        self.state, self.drafts = state, drafts
        self.catalog_provider, self.history_provider = catalog_provider, history_provider
        self.source_provider = source_provider
        self.clock = clock or time.time

    @staticmethod
    def writer_enabled() -> bool:
        from guardoath.state_contracts import new_catalog_publication_state_enabled
        return new_catalog_publication_state_enabled()

    @staticmethod
    def _identity(actor, review_id=None):
        if (type(actor) is not str or not _OWNER.fullmatch(actor)
                or (review_id is not None and
                    (type(review_id) is not str or not _HEX.fullmatch(review_id)))):
            raise ValueError("Invalid catalog owner or review reference")

    def _source(self) -> str:
        try:
            value = self.source_provider()
            if type(value) is not str or not _SHA.fullmatch(value):
                raise ValueError()
            return value
        except Exception as exc:
            raise PublicationUnavailable("Packaged source provenance unavailable") from exc

    def _proposal(self, actor: str, review_id: str, operation: str) -> dict:
        self._identity(actor, review_id)
        if type(operation) is not str or operation not in _OPERATIONS:
            raise ValueError("Unsupported catalog publication operation")
        base_source_sha = self._source()
        try:
            review = self.drafts.read_review(actor, review_id)
            if review is None:
                raise PublicationConflict("Reviewed catalog draft not found")
            draft = self.drafts.read(actor, review["draft_id"])
            if draft is None:
                raise PublicationUnavailable("Signed reviewed draft unavailable")
            # One independently read source snapshot feeds BOTH the manifest
            # calculation and compiler; no second registry can change the plan.
            source = deepcopy(self.catalog_provider())
            history = deepcopy(self.history_provider())
            manifest = reviewed_metadata_manifest(lambda: source, draft=draft, review=review)
            plan = source_plan_from_manifest(manifest)
            compiler = candidate_release if operation == "publish_metadata" else candidate_rollback_release
            compiler(plan, lambda: source, history)
            if self._source() != base_source_sha:
                raise PublicationConflict("Packaged source changed")
        except (CatalogManifestConflict, SourceReleaseConflict) as exc:
            raise PublicationConflict("Reviewed catalog source or inverse changed") from exc
        except (PublicationConflict, PublicationUnavailable):
            raise
        except (DraftUnavailable, CatalogManifestUnavailable, PublicationSourceUnavailable,
                KeyError, TypeError, ValueError) as exc:
            raise PublicationUnavailable("Catalog publication evidence unavailable") from exc
        except Exception as exc:
            raise PublicationUnavailable("Catalog publication source unavailable") from exc
        return {"review_id": review_id, "draft_id": draft["draft_id"],
            "review_sha256": document_hash(review), "draft_sha256": document_hash(draft),
            "base_source_sha": base_source_sha, "operation": operation,
            "registry_version": manifest["registry_version"],
            "candidate_registry_version": manifest["candidate_registry_version"],
            "baseline_sha256": manifest["reviewed_source_sha256"],
            "candidate_metadata_sha256": manifest["candidate_metadata_sha256"],
            "target": deepcopy(plan["target"]), "plan": plan,
            "plan_sha256": document_hash(plan)}

    def preview(self, *, actor: str, review_id: str, operation: str) -> dict:
        proposal = self._proposal(actor, review_id, operation)
        control = self.state.read_control()
        return {"status": "publication_preview", **{name: proposal[name] for name in (
            "review_id", "draft_id", "base_source_sha", "operation", "registry_version",
            "candidate_registry_version", "baseline_sha256", "candidate_metadata_sha256", "target")},
            "changed_metadata": deepcopy(proposal["plan"]["changed_metadata"]),
            "expected_control_hash": document_hash(control) if control else EMPTY_CONTROL_HASH,
            "control_available": control is None, "writer_enabled": self.writer_enabled(),
            "confirmation": ("PUBLISH " if operation == "publish_metadata" else "REVERSE ") + review_id[-8:],
            "published": False, "tenant_policies_changed": False, "detector_behavior_changed": False,
            "remote_published": False, "ci_verified": False, "deployed": False}

    def request(self, *, actor: str, review_id: str, operation: str,
                expected_source_sha: str, expected_control_hash: str, reason: str,
                action_reference: str, confirmation: str) -> dict:
        # Route mounting is not the sole gate; direct callers cannot introduce
        # new persistent state from a support-only rollback source.
        if not self.writer_enabled():
            raise PublicationUnavailable("Catalog publication requests are not enabled")
        self._identity(actor, review_id)
        if (type(operation) is not str or operation not in _OPERATIONS or type(action_reference) is not str
                or not _REFERENCE.fullmatch(action_reference)
                or type(expected_source_sha) is not str or not _SHA.fullmatch(expected_source_sha)
                or type(expected_control_hash) is not str or not _HEX.fullmatch(expected_control_hash)
                or not _safe_text(reason, 240) or len(reason) < 10
                or confirmation != ("PUBLISH " if operation == "publish_metadata" else "REVERSE ") + review_id[-8:]):
            raise ValueError("Exact publication confirmation, source and action reference required")
        request_id = self.state.request_id(actor, action_reference)
        request_hash = document_hash({"actor_sub": actor, "review_id": review_id,
            "operation": operation, "expected_source_sha": expected_source_sha,
            "expected_control_hash": expected_control_hash, "reason": reason,
            "action_reference": action_reference, "confirmation": confirmation})
        prior, _ = self.state.history(request_id)
        if prior is not None:
            if prior["actor_sub"] != actor or prior["request_hash"] != request_hash:
                raise PublicationConflict("Action reference already identifies a different request")
            # Historical replay does not reopen the source or authorize a new
            # attempt; it remains useful after a deployment/source change.
            return {**self.inspect(actor=actor, request_id=request_id), "action_reference": action_reference}
        proposal = self._proposal(actor, review_id, operation)
        if proposal["base_source_sha"] != expected_source_sha:
            raise PublicationConflict("Deployed source changed; review publication again")
        control = self.state.read_control()
        if control is not None or expected_control_hash != EMPTY_CONTROL_HASH:
            raise PublicationConflict("Another catalog release owns source preparation")
        try:
            stamp = self.clock()
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not 1 <= stamp <= 253402300799:
                raise ValueError()
            created_at = int(stamp)
        except Exception as exc:
            raise PublicationUnavailable("Publication clock unavailable") from exc
        self.state.reserve({**proposal, "request_id": request_id, "actor_sub": actor,
            "reason": reason, "created_at": created_at, "request_hash": request_hash},
            expected_control_hash=expected_control_hash)
        return {**self.inspect(actor=actor, request_id=request_id), "action_reference": action_reference}

    def inspect(self, *, actor: str, request_id: str) -> dict | None:
        self._identity(actor)
        if type(request_id) is not str or not _HEX.fullmatch(request_id):
            raise ValueError("Invalid catalog publication reference")
        intent, prepared = self.state.history(request_id)
        if intent is None or intent["actor_sub"] != actor:
            return None
        control = self.state.read_control()
        if (control is None or control["request_id"] != request_id
                or control["intent_hash"] != document_hash(intent)):
            raise PublicationUnavailable("Publication control integrity unavailable")
        status = {"REQUESTED": "requested", "PREPARING": "preparing",
                  "SOURCE_PREPARED": "source_prepared"}[control["state"]]
        if (status == "source_prepared") != (prepared is not None):
            raise PublicationUnavailable("Source preparation receipt unavailable")
        return {"status": status, "request_id": request_id, "integrity_verified": True,
            "intent_sha256": document_hash(intent), **{name: intent[name] for name in (
                "review_id", "draft_id", "operation", "base_source_sha", "registry_version",
                "candidate_registry_version", "baseline_sha256", "candidate_metadata_sha256",
                "created_at", "target", "reason")},
            "changed_metadata": deepcopy(intent["plan"]["changed_metadata"]),
            "source_preparation": ({name: prepared[name] for name in (
                "candidate_source_sha", "history_sha256", "recorded_at")} if prepared else None),
            "remote_published": False, "ci_verified": False, "deployed": False,
            "published": False, "tenant_policies_changed": False, "detector_behavior_changed": False}

    def recover(self, *, actor: str, action_reference: str) -> dict | None:
        self._identity(actor)
        if type(action_reference) is not str or not _REFERENCE.fullmatch(action_reference):
            raise ValueError("Invalid catalog publication action reference")
        result = self.inspect(actor=actor, request_id=self.state.request_id(actor, action_reference))
        return {**result, "action_reference": action_reference} if result is not None else None
