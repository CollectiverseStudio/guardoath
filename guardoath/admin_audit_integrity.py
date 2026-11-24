"""Tamper evidence for new persistent owner audit receipts.

Existing receipts without a signature remain distinguishable as legacy and
cannot be described as cryptographically verified. The MAC covers only the
approved, redacted DynamoDB fields and their typed representations.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re

SIGNED_FIELDS = (
    "event_id", "tenant_id", "actor_sub", "operation", "when_utc", "reason",
    "prior_revision", "new_revision", "old_status", "new_status",
    "rule_id", "stage", "old_action", "new_action", "old_limit", "new_limit",
    "old_key_count", "new_key_count", "read_resource",
    "old_subscription_status", "new_subscription_status", "provider_status",
    "old_enabled", "new_enabled", "target_fingerprint", "related_event_id",
    "request_fingerprint", "plan_limit", "cap_limit",
)
_DOMAIN = b"guardoath-admin-audit-integrity-v1\x00"


def _canonical(item: dict) -> bytes:
    if not isinstance(item, dict):
        raise ValueError("Malformed audit item")
    approved = {}
    for field in SIGNED_FIELDS:
        if field not in item:
            continue
        typed = item[field]
        if (not isinstance(typed, dict) or len(typed) != 1 or
                not any(k in typed and isinstance(typed[k], str) for k in ("S", "N"))):
            raise ValueError("Malformed typed audit field")
        approved[field] = typed
    return json.dumps(approved, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def audit_mac(item: dict, key: bytes) -> str:
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("Independent audit signing secret required")
    return hmac.new(key, _DOMAIN + _canonical(item), hashlib.sha256).hexdigest()


def seal_audit(item: dict, key: bytes) -> dict:
    if "integrity_mac" in item or any(k not in SIGNED_FIELDS for k in item):
        raise ValueError("Unexpected audit item field")
    return {**item, "integrity_mac": {"S": audit_mac(item, key)}}


def verify_audit(item: dict, key: bytes) -> bool:
    if not isinstance(item, dict):
        return False
    # A caller supplying a complete receipt must not attach fields outside
    # the signed schema and still describe the whole row as verified.
    if any(name not in SIGNED_FIELDS and name != "integrity_mac" for name in item):
        return False
    field = item.get("integrity_mac")
    if (not isinstance(field, dict) or set(field) != {"S"} or
            not isinstance(field["S"], str) or
            not re.fullmatch(r"[0-9a-f]{64}", field["S"])):
        return False
    try:
        return hmac.compare_digest(field["S"], audit_mac(
            {name: item[name] for name in SIGNED_FIELDS if name in item}, key))
    except (TypeError, ValueError):
        return False
