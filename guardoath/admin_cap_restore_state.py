"""Atomic local request-cap restoration and immutable signed receipt recovery.

The caller verifies the paid provider entitlement. This boundary independently
restricts the only durable change to removing a coherent saved local cap and
restoring its saved plan allowance. Historical reads never require a provider,
current tenant configuration, or an enabled writer phase.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import hmac
import json
import re
from typing import Callable

from guardoath.admin_actions import IDEMPOTENCY, REASON, TENANT
from guardoath.admin_audit_integrity import seal_audit, verify_audit
from guardoath.admin_directory import DirectoryUnavailable, _strict_record_json
from guardoath.subscription_reconciliation import StripeBinding
from guardoath.tenant_record import MAX_RECORD_BYTES, TenantRecord

CONTRACT = "subscription-cap-restoration-v1"
OPERATION = "tenant.allowance_restore"
FIELDS = (
    "event_id", "tenant_id", "actor_sub", "operation", "when_utc", "reason",
    "prior_revision", "new_revision", "old_status", "new_status",
    "old_subscription_status", "new_subscription_status", "provider_status",
    "old_limit", "new_limit", "plan_limit", "cap_limit", "target_fingerprint",
    "request_fingerprint",
)
NUMBERS = frozenset({"prior_revision", "new_revision", "old_limit", "new_limit", "plan_limit", "cap_limit"})
_IDENTIFIER = re.compile(r"^[a-f0-9]{64}$")
_SUBJECT = re.compile(r"^[A-Za-z0-9_-]{12,128}$")
_TABLE = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")
_UTC = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
_MAX_REVISION = (1 << 53) - 1


class CapRestoreStateConflict(Exception):
    """A reviewed tenant or an immutable request reference no longer matches."""


class CapRestoreStateUnavailable(Exception):
    """A trustworthy restoration result cannot currently be established."""


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _validate_fields(fields: dict) -> None:
    if not isinstance(fields, dict) or set(fields) != set(FIELDS):
        raise ValueError("Invalid restoration receipt fields")
    if any(type(fields[name]) is not int for name in NUMBERS):
        raise ValueError("Invalid restoration receipt numbers")
    if any(not isinstance(fields[name], str) for name in set(FIELDS) - NUMBERS):
        raise ValueError("Invalid restoration receipt strings")
    if (not all(_matches(_IDENTIFIER, fields[name]) for name in
                ("event_id", "request_fingerprint", "target_fingerprint"))
            or not _matches(TENANT, fields["tenant_id"])
            or not _matches(_SUBJECT, fields["actor_sub"])
            or not _matches(REASON, fields["reason"])
            or fields["operation"] != OPERATION
            or not _matches(_UTC, fields["when_utc"])
            or not 1 <= fields["prior_revision"] < _MAX_REVISION
            or fields["new_revision"] != fields["prior_revision"] + 1
            or fields["old_status"] not in ("active", "disabled")
            or fields["new_status"] != fields["old_status"]
            or any(fields[name] != "active" for name in
                   ("old_subscription_status", "new_subscription_status", "provider_status"))
            or fields["old_limit"] != fields["cap_limit"]
            or not 0 <= fields["cap_limit"] < fields["plan_limit"] <= 1_000_000_000
            or fields["new_limit"] != fields["plan_limit"]):
        raise ValueError("Invalid restoration receipt semantics")
    datetime.strptime(fields["when_utc"], "%Y-%m-%dT%H:%M:%SZ")


def decode_cap_restore_receipt(item: dict, *, key: bytes | None, event_id: str) -> dict:
    """One strict decoder shared by dedicated and generic signed-audit readers."""
    try:
        if (not _matches(_IDENTIFIER, event_id) or not isinstance(item, dict)
                or set(item) != {*FIELDS, "integrity_mac"} or not verify_audit(item, key)):
            raise ValueError("Invalid signed restoration receipt")
        fields = {}
        for name in FIELDS:
            value = item[name]
            tag = "N" if name in NUMBERS else "S"
            if not isinstance(value, dict) or set(value) != {tag} or not isinstance(value[tag], str):
                raise ValueError("Invalid typed restoration receipt")
            if tag == "N":
                if not re.fullmatch(r"(?:0|[1-9][0-9]{0,15})", value[tag]):
                    raise ValueError("Invalid canonical restoration number")
                fields[name] = int(value[tag])
            else:
                fields[name] = value[tag]
        _validate_fields(fields)
        if fields["event_id"] != event_id:
            raise ValueError("Restoration receipt identity mismatch")
        return {**fields, "status": "applied", "integrity_status": "signed_verified", "replayed": True}
    except (TypeError, ValueError, OverflowError) as exc:
        raise CapRestoreStateUnavailable("Signed cap restoration receipt unavailable") from exc


class DynamoCapRestoreState:
    def __init__(self, client, table: str, key: bytes):
        if (not all(callable(getattr(client, name, None)) for name in ("get_item", "transact_write_items"))
                or not _matches(_TABLE, table) or not isinstance(key, bytes) or len(key) < 32):
            raise ValueError("Independent signed cap restoration state required")
        self.client, self.table_name, self.key, self.audit_key = client, table, key, key

    def _digest(self, domain: bytes, value) -> str:
        body = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
        return hmac.new(self.key, domain + body, hashlib.sha256).hexdigest()

    def action_id(self, actor: str, idempotency_key: str) -> str:
        if not _matches(_SUBJECT, actor) or not _matches(IDEMPOTENCY, idempotency_key):
            raise ValueError("Invalid cap restoration action reference")
        return self._digest(b"guardoath-cap-restore-action-v1\x00", [actor, idempotency_key])

    def request_fingerprint(self, *, actor: str, tenant_id: str, expected_revision: int,
                            preview_reference: str, confirmation: str, reason: str) -> str:
        if (not _matches(_SUBJECT, actor) or not _matches(TENANT, tenant_id)
                or type(expected_revision) is not int or not 1 <= expected_revision < _MAX_REVISION
                or not isinstance(preview_reference, str) or not 1 <= len(preview_reference) <= 16384
                or not preview_reference.isascii() or any(ord(ch) < 33 or ord(ch) > 126 for ch in preview_reference)
                or not isinstance(confirmation, str) or not 1 <= len(confirmation) <= 160
                or not confirmation.isascii() or not _matches(REASON, reason)):
            raise ValueError("Invalid cap restoration request")
        return self._digest(b"guardoath-cap-restore-request-v1\x00",
                            [actor, tenant_id, expected_revision, preview_reference, confirmation, reason])

    def history(self, action_id: str, *, request_fingerprint: str | None = None) -> dict | None:
        if (not _matches(_IDENTIFIER, action_id)
                or (request_fingerprint is not None and not _matches(_IDENTIFIER, request_fingerprint))):
            raise ValueError("Invalid cap restoration history reference")
        try:
            response = self.client.get_item(TableName=self.table_name,
                Key={"event_id": {"S": action_id}}, ConsistentRead=True)
            if not isinstance(response, dict):
                raise CapRestoreStateUnavailable("Cap restoration history unavailable")
            if "Item" not in response:
                return None
            receipt = decode_cap_restore_receipt(response["Item"], key=self.key, event_id=action_id)
            if (request_fingerprint is not None
                    and not hmac.compare_digest(receipt["request_fingerprint"], request_fingerprint)):
                raise CapRestoreStateConflict("Idempotency reference already used")
            return receipt
        except (CapRestoreStateConflict, CapRestoreStateUnavailable):
            raise
        except Exception as exc:
            raise CapRestoreStateUnavailable("Cap restoration history unavailable") from exc

    @staticmethod
    def _matching_prior(fields: dict, receipt: dict | None) -> dict | None:
        if receipt is not None and any(receipt[name] != fields[name] for name in FIELDS if name != "when_utc"):
            raise CapRestoreStateConflict("Idempotency reference already used")
        return receipt

    @staticmethod
    def _validate_documents(fields: dict, original_record: str, updated_record: str) -> None:
        if any(not isinstance(body, str) or not body or len(body.encode()) > MAX_RECORD_BYTES
               for body in (original_record, updated_record)):
            raise ValueError("Invalid cap restoration tenant document")
        try:
            before, after = (_strict_record_json(body) for body in (original_record, updated_record))
        except (TypeError, ValueError, DirectoryUnavailable) as exc:
            raise CapRestoreStateUnavailable("Saved tenant document unavailable") from exc
        if (not isinstance(before, dict) or not isinstance(after, dict)
                or {"usage", "revision"} & (set(before) | set(after))
                or before.get("tenant_id") != fields["tenant_id"]
                or before.get("demo") is not False or before.get("billing_mode") != "stripe"
                or before.get("status") != fields["old_status"]
                or before.get("subscription_status") != "active" or before.get("overage") != "block"
                or type(before.get("admin_monthly_allowance_cap")) is not int
                or before["admin_monthly_allowance_cap"] != fields["cap_limit"]
                or type(before.get("stripe_plan_monthly_allowance")) is not int
                or before["stripe_plan_monthly_allowance"] != fields["plan_limit"]
                or type(before.get("monthly_allowance")) is not int
                or before["monthly_allowance"] != fields["old_limit"]):
            raise CapRestoreStateConflict("Saved local cap is not eligible for restoration")
        StripeBinding(fields["tenant_id"], before.get("payer_id"), before.get("stripe_subscription_id"))
        expected = {**before, "monthly_allowance": fields["new_limit"], "updated_at": fields["when_utc"]}
        expected.pop("admin_monthly_allowance_cap")
        # JSON type identity matters: Python equality treats false as 0 and an
        # integral float as an integer, which must not authorize other edits.
        canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if canonical(after) != canonical(expected):
            raise CapRestoreStateConflict("Restoration may only remove the saved local cap")
        TenantRecord.model_validate({**before, "revision": fields["prior_revision"]})
        TenantRecord.model_validate({**after, "revision": fields["new_revision"]})

    def commit(self, fields: dict, *, tenant_table: str, original_record: str, updated_record: str,
               before_write: Callable[[], None] | None = None) -> dict:
        _validate_fields(fields)
        if not _matches(_TABLE, tenant_table) or tenant_table == self.table_name:
            raise ValueError("Separate authoritative tenant table required")
        previous = self._matching_prior(fields, self.history(fields["event_id"],
                                         request_fingerprint=fields["request_fingerprint"]))
        if previous is not None:
            return previous
        from guardoath.state_contracts import new_cap_restoration_state_enabled
        if not new_cap_restoration_state_enabled():
            raise CapRestoreStateUnavailable("New cap restoration requires an accepted compatibility release")
        self._validate_documents(fields, original_record, updated_record)
        item = seal_audit({name: {"N": str(fields[name])} if name in NUMBERS else {"S": fields[name]}
                           for name in FIELDS}, self.key)
        # Provider/review expiry may be reached during the strongly consistent
        # history read or document validation. The service supplies this final
        # trusted guard; an already-existing receipt never invokes it.
        if before_write is not None:
            if not callable(before_write):
                raise ValueError("Invalid cap restoration write guard")
            before_write()
        try:
            response = self.client.transact_write_items(TransactItems=[
                {"Update": {"TableName": tenant_table, "Key": {"tenant_id": {"S": fields["tenant_id"]}},
                    "UpdateExpression": "SET #record = :record, #revision = :next",
                    "ConditionExpression": "attribute_exists(tenant_id) AND #revision = :prior AND #record = :original",
                    "ExpressionAttributeNames": {"#record": "record", "#revision": "revision"},
                    "ExpressionAttributeValues": {":record": {"S": updated_record}, ":original": {"S": original_record},
                        ":prior": {"N": str(fields["prior_revision"])}, ":next": {"N": str(fields["new_revision"])}}}},
                {"Put": {"TableName": self.table_name, "Item": item,
                         "ConditionExpression": "attribute_not_exists(event_id)"}}])
            if not isinstance(response, dict):
                raise RuntimeError("Unrecognized cap restoration transaction response")
        except Exception as exc:
            # One strongly consistent receipt read may resolve a lost response.
            # An absent receipt is not permission to issue another transaction.
            previous = self._matching_prior(fields, self.history(fields["event_id"],
                                             request_fingerprint=fields["request_fingerprint"]))
            if previous is not None:
                return previous
            detail = getattr(exc, "response", {})
            code = detail.get("Error", {}).get("Code") if isinstance(detail, dict) else None
            if code == "ConditionalCheckFailedException":
                raise CapRestoreStateConflict("Tenant or restoration reference changed") from exc
            if code == "TransactionCanceledException":
                reasons = detail.get("CancellationReasons")
                if (isinstance(reasons, list) and reasons
                        and all(isinstance(reason, dict) and reason.get("Code") in ("None", "ConditionalCheckFailed")
                                for reason in reasons)
                        and any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons)):
                    raise CapRestoreStateConflict("Tenant or restoration reference changed") from exc
            raise CapRestoreStateUnavailable("Cap restoration result unavailable; retain the original reference") from exc
        return {**fields, "status": "applied", "integrity_status": "signed_verified", "replayed": False}
