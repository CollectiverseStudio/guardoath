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
        selected = {name: preview[name] for name in properties}
        if not isinstance(selected["changes"], dict) or not selected["changes"]:
            raise ValueError("A changed catalog description is required")
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
