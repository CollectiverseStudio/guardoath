"""Owner-scoped immutable catalog review drafts in the existing audit table.

Drafts are NOT catalog publications and must not alter tenant policy.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import re


class DraftConflict(Exception):
    """An idempotency key was already used for another proposal."""


class DraftUnavailable(Exception):
    """Signed draft persistence or integrity cannot be verified."""


_OWNER = re.compile(r"^[0-9a-fA-F-]{20,80}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_TABLE = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")
_REASON = re.compile(r"^[^\x00-\x1f\x7f]{10,240}$")
_DOMAIN = b"guardoath-admin-catalog-draft-v1\x00"


class AdminCatalogDrafts:
    """A conditional, integrity-checked metadata-only record per review token.

    The audit table retains its ordinary event_id partition key and IAM policy;
    the existing owner admin backend grants access after its Cognito/MFA checks.
    """

    def __init__(self, ddb, table: str, signing_key: bytes, clock=None):
        if (not callable(getattr(ddb, "put_item", None)) or
                not callable(getattr(ddb, "get_item", None)) or
                not isinstance(table, str) or not _TABLE.fullmatch(table) or
                not isinstance(signing_key, bytes) or len(signing_key) < 32):
            raise ValueError("Separate signed admin audit table required")
        self.ddb, self.table, self.signing_key = ddb, table, signing_key
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _digest(self, tag: bytes, content: bytes) -> str:
        return hmac.new(self.signing_key, _DOMAIN + tag + content,
                        hashlib.sha256).hexdigest()

    def _draft_id(self, actor: str, nonce: str) -> str:
        return self._digest(b"id\x00", actor.encode("ascii") + b"\x00" + nonce.encode("ascii"))

    def _mac(self, body: dict) -> str:
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False).encode("ascii")
        return self._digest(b"row\x00", canonical)

    def read(self, actor: str, draft_id: str) -> dict | None:
        if (not isinstance(actor, str) or not _OWNER.fullmatch(actor) or
                not isinstance(draft_id, str) or not _HEX.fullmatch(draft_id)):
            raise ValueError("Invalid owner or catalog draft")
        try:
            response = self.ddb.get_item(
                TableName=self.table, Key={"event_id": {"S": draft_id}},
                ConsistentRead=True)
            row = response.get("Item")
            if row is None:
                return None
            fields = {"event_id", "actor_sub", "operation", "document", "integrity_mac"}
            if (not isinstance(row, dict) or set(row) != fields or any(
                    not isinstance(value, dict) or set(value) != {"S"} or
                    not isinstance(value["S"], str) for value in row.values())):
                raise DraftUnavailable("Malformed catalog draft receipt")
            body = {name: row[name]["S"] for name in fields - {"integrity_mac"}}
            if (body["event_id"] != draft_id or body["actor_sub"] != actor or
                    body["operation"] != "catalog.draft" or
                    not hmac.compare_digest(row["integrity_mac"]["S"], self._mac(body))):
                raise DraftUnavailable("Catalog draft integrity unavailable")
            document = json.loads(body["document"])
            if (not isinstance(document, dict) or document.get("draft_id") != draft_id or
                    document.get("status") != "draft_saved" or
                    document.get("published") is not False or
                    document.get("tenant_policies_changed") is not False):
                raise DraftUnavailable("Invalid catalog draft")
            return document
        except DraftUnavailable:
            raise
        except Exception as exc:
            raise DraftUnavailable("Catalog draft read unavailable") from exc

    def save(self, *, actor: str, nonce: str, reason: str, preview: dict,
             title: str, use_case: str) -> dict:
        if (not isinstance(actor, str) or not _OWNER.fullmatch(actor) or
                not isinstance(nonce, str) or not _NONCE.fullmatch(nonce) or
                not isinstance(reason, str) or not _REASON.fullmatch(reason) or
                not isinstance(title, str) or not 3 <= len(title) <= 120 or
                not isinstance(use_case, str) or not 3 <= len(use_case) <= 500 or
                not isinstance(preview, dict) or preview.get("status") != "preview_only" or
                preview.get("published") is not False or
                preview.get("persisted") is not False or
                preview.get("tenant_policies_changed") is not False or
                preview.get("detector_behavior_changed") is not False or
                any(not isinstance(preview.get(k), str) or not _HEX.fullmatch(preview[k])
                    for k in ("baseline_sha256", "proposal_sha256"))):
            raise ValueError("A verified catalog proposal and audit reason are required")
        properties = ("registry_version", "candidate_registry_version", "category_id",
                      "check_id", "rule_id", "changes", "baseline_sha256", "proposal_sha256")
        try:
            selected = {name: preview[name] for name in properties}
        except KeyError as exc:
            raise ValueError("A complete catalog preview is required") from exc
        changes = selected["changes"]
        if (not isinstance(changes, dict) or not changes or
                set(changes) - {"title", "use_case"} or
                preview.get("proposed_title") != title.strip() or
                preview.get("proposed_use_case") != use_case.strip()):
            raise ValueError("Catalog draft must match its verified description preview")
        for name, delta in changes.items():
            if (not isinstance(delta, dict) or set(delta) != {"before", "after"} or
                    not isinstance(delta["before"], str) or
                    not isinstance(delta["after"], str) or
                    delta["before"] == delta["after"] or
                    delta["after"] != (title.strip() if name == "title" else use_case.strip())):
                raise ValueError("Catalog draft changes differ from verified preview")
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise DraftUnavailable("Catalog draft clock unavailable")
        draft_id = self._draft_id(actor, nonce)
        record = {
            **selected, "draft_id": draft_id, "status": "draft_saved", "published": False,
            "tenant_policies_changed": False, "detector_behavior_changed": False,
            "title": title.strip(), "use_case": use_case.strip(), "reason": reason,
            "when_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False)
        if len(encoded.encode("ascii")) > 4096:
            raise ValueError("Oversized catalog draft")
        body = {"event_id": draft_id, "actor_sub": actor, "operation": "catalog.draft",
                "document": encoded}
        row = {field: {"S": val} for field, val in body.items()}
        row["integrity_mac"] = {"S": self._mac(body)}
        try:
            self.ddb.put_item(TableName=self.table, Item=row,
                              ConditionExpression="attribute_not_exists(event_id)")
        except Exception as exc:
            prior = self.read(actor, draft_id)
            if prior is not None:
                if (prior.get("reason") == reason and prior.get("title") == title.strip() and
                        prior.get("use_case") == use_case.strip() and
                        all(prior.get(name) == selected[name] for name in properties)):
                    return prior
                raise DraftConflict("Idempotency key reused for another draft") from exc
            raise DraftUnavailable("Catalog draft persistence unavailable") from exc
        return record


    def read_review(self, actor: str, review_id: str) -> dict | None:
        """Read an immutable, owner-bound catalog review receipt; no publication."""
        if (not isinstance(actor, str) or not _OWNER.fullmatch(actor) or
                not isinstance(review_id, str) or not _HEX.fullmatch(review_id)):
            raise ValueError("Invalid owner or catalog review")
        try:
            response = self.ddb.get_item(
                TableName=self.table, Key={"event_id": {"S": review_id}},
                ConsistentRead=True)
            row = response.get("Item")
            if row is None:
                return None
            fields = {"event_id", "actor_sub", "operation", "document", "integrity_mac"}
            if (not isinstance(row, dict) or set(row) != fields or any(
                    not isinstance(value, dict) or set(value) != {"S"} or
                    not isinstance(value["S"], str) for value in row.values())):
                raise DraftUnavailable("Malformed catalog review receipt")
            body = {name: row[name]["S"] for name in fields - {"integrity_mac"}}
            if (body["event_id"] != review_id or body["actor_sub"] != actor or
                    body["operation"] != "catalog.review" or
                    not hmac.compare_digest(row["integrity_mac"]["S"], self._mac(body))):
                raise DraftUnavailable("Catalog review integrity unavailable")
            record = json.loads(body["document"])
            if (not isinstance(record, dict) or record.get("review_id") != review_id or
                    record.get("reviewer_sub") != actor or
                    record.get("status") != "review_recorded" or
                    record.get("published") is not False or
                    record.get("tenant_policies_changed") is not False or
                    record.get("detector_behavior_changed") is not False or
                    any(not isinstance(record.get(k), str) or not _HEX.fullmatch(record[k])
                        for k in ("draft_id", "baseline_sha256", "proposal_sha256"))):
                raise DraftUnavailable("Invalid catalog review")
            return record
        except DraftUnavailable:
            raise
        except Exception as exc:
            raise DraftUnavailable("Catalog review read unavailable") from exc

    def review(self, *, actor: str, draft_id: str, nonce: str, reason: str,
               confirmation: str, baseline_sha256: str,
               proposal_sha256: str) -> dict:
        """Persist a signed review of one current source-bound draft.

        The caller must independently recheck the authoritative registry immediately
        before this write. A review is NOT an approval to publish and grants no
        tenant access, rule activation, or detector changes.
        """
        if (not isinstance(actor, str) or not _OWNER.fullmatch(actor) or
                not isinstance(draft_id, str) or not _HEX.fullmatch(draft_id) or
                not isinstance(nonce, str) or not _NONCE.fullmatch(nonce) or
                not isinstance(reason, str) or not _REASON.fullmatch(reason) or
                confirmation != "REVIEW " + draft_id[-8:] or
                not isinstance(baseline_sha256, str) or not _HEX.fullmatch(baseline_sha256) or
                not isinstance(proposal_sha256, str) or not _HEX.fullmatch(proposal_sha256)):
            raise ValueError("Valid reviewed draft and typed confirmation required")
        draft = self.read(actor, draft_id)
        if draft is None or draft["baseline_sha256"] != baseline_sha256 or (
                draft["proposal_sha256"] != proposal_sha256):
            raise DraftConflict("Catalog review proposal or source changed")
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise DraftUnavailable("Catalog review clock unavailable")
        review_id = self._digest(b"review\x00", actor.encode("ascii") + b"\x00"
                                 + draft_id.encode("ascii") + b"\x00" + nonce.encode("ascii"))
        record = {"review_id": review_id, "draft_id": draft_id, "reviewer_sub": actor,
                  "baseline_sha256": baseline_sha256, "proposal_sha256": proposal_sha256,
                  "status": "review_recorded", "published": False,
                  "tenant_policies_changed": False, "detector_behavior_changed": False,
                  "reason": reason,
                  "when_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False)
        body = {"event_id": review_id, "actor_sub": actor, "operation": "catalog.review",
                "document": encoded}
        row = {field: {"S": val} for field, val in body.items()}
        row["integrity_mac"] = {"S": self._mac(body)}
        try:
            self.ddb.put_item(TableName=self.table, Item=row,
                              ConditionExpression="attribute_not_exists(event_id)")
        except Exception as exc:
            prior = self.read_review(actor, review_id)
            if prior is not None:
                if (prior.get("draft_id") == draft_id and prior.get("reason") == reason and
                        prior.get("baseline_sha256") == baseline_sha256 and
                        prior.get("proposal_sha256") == proposal_sha256):
                    return prior
                raise DraftConflict("Idempotency key reused for another review") from exc
            raise DraftUnavailable("Catalog review persistence unavailable") from exc
        return record
