"""Bounded, read-only DynamoDB tenant-directory page for GuardOath administrators.

Does not replace the tenant record, and never returns full raw DynamoDB items.
Uses opaque authenticated cursors so callers cannot submit arbitrary scan keys.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timezone
from typing import Callable

MAX_PAGE = 50
CURSOR_TTL_SECONDS = 900
# Caller identity is independently verified, never taken from query parameters.
OWNER_SUB = re.compile(r"^[0-9a-fA-F-]{20,80}$")


class InvalidCursor(ValueError):
    pass


class DirectoryUnavailable(RuntimeError):
    """The DynamoDB source is unavailable or cannot be safely projected."""


_TENANT = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
_MONTH = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
_NONNEGATIVE = re.compile(r"^(0|[1-9][0-9]{0,17})$")


def _number_attribute(value: object) -> int:
    """Read only canonical, bounded DynamoDB nonnegative Number attributes."""
    if (not isinstance(value, dict) or set(value) != {"N"}
            or not isinstance(value["N"], str) or not _NONNEGATIVE.fullmatch(value["N"])):
        raise DirectoryUnavailable("Malformed tenant directory record")
    return int(value["N"])


def _project_item(item: object, month: str) -> dict:
    """Validate source identity before presenting administrative controls.

    A corrupt or missing demo flag must never be projected as a paying
    customer; billing state and usage must not be inferred by stringification.
    """
    if not isinstance(item, dict):
        raise DirectoryUnavailable("Malformed tenant directory record")
    key = item.get("tenant_id")
    record = item.get("record")
    if (not isinstance(key, dict) or set(key) != {"S"}
            or not isinstance(key["S"], str) or not _TENANT.fullmatch(key["S"])
            or not isinstance(record, dict) or set(record) != {"S"}
            or not isinstance(record["S"], str)
            or len(record["S"].encode("utf-8")) > 350_000):
        raise DirectoryUnavailable("Malformed tenant directory record")
    try:
        doc = json.loads(record["S"])
    except (ValueError, TypeError) as exc:
        raise DirectoryUnavailable("Malformed tenant directory record") from exc
    if not isinstance(doc, dict) or doc.get("tenant_id") != key["S"]:
        raise DirectoryUnavailable("Tenant directory identity mismatch")
    name = doc.get("display_name")
    status = doc.get("status")
    demo = doc.get("demo")
    subscription = doc.get("subscription_status")
    updated = doc.get("updated_at")
    if (not isinstance(name, str) or not 1 <= len(name) <= 100
            or status not in ("active", "disabled")
            or type(demo) is not bool
            or subscription not in (None, "inactive", "active", "past_due", "canceled")
            or (updated is not None and
                (not isinstance(updated, str) or len(updated) > 40))):
        raise DirectoryUnavailable("Ambiguous tenant account or subscription metadata")
    revision = _number_attribute(item.get("revision"))
    usage_count = None
    if "usage" in item:
        usage = item["usage"]
        if (not isinstance(usage, dict) or set(usage) != {"M"}
                or not isinstance(usage["M"], dict) or len(usage["M"]) > 120):
            raise DirectoryUnavailable("Malformed tenant usage record")
        for period, value in usage["M"].items():
            if not isinstance(period, str) or not _MONTH.fullmatch(period):
                raise DirectoryUnavailable("Malformed tenant usage record")
            count = _number_attribute(value)
            if period == month:
                usage_count = count
    allowance = doc.get("monthly_allowance")
    if type(allowance) is not int or not 0 <= allowance <= 1_000_000_000:
        allowance = None  # Legacy/invalid allowance is unknown, not zero.
    hashes = doc.get("key_hashes")
    key_count = (len(hashes) if isinstance(hashes, list) and len(hashes) <= 2
                 and all(isinstance(value, str) for value in hashes) else None)
    return {
        "tenant_id": key["S"],
        "display_name": name,
        "status": status,
        "demo": demo,
        "subscription_status": subscription,  # None = unverified legacy state.
        "monthly_allowance": allowance,
        "api_key_count": key_count,
        "usage_month": month,
        "usage_month_count": usage_count,
        "subscription_source": "persisted tenant record; not live Stripe verification",
        "revision": revision,
        "updated_at": updated,
    }


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > 4096:
        raise InvalidCursor("Invalid pagination cursor")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidCursor("Invalid pagination cursor") from exc


class DynamoAdminDirectory:
    """Read-only adapter around a boto3 DynamoDB client.

    The scan is hard-limited to the requested page. Results may be fewer than
    requested when search filters are used; never scan unbounded pages to fill.
    """
    def __init__(self, client, table: str, signing_key: bytes, clock: Callable[[], float] = time.time):
        if not table or not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("Administrator pagination requires a table and a strong signing key")
        self.client, self.table, self.key, self.clock = client, table, signing_key, clock

    def _sign(self, payload: dict) -> str:
        encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        tag = _b64(hmac.new(self.key, encoded.encode(), hashlib.sha256).digest())
        return encoded + "." + tag

    def _owner_label(self, owner_sub: str) -> str:
        # Avoid publishing the raw Cognito subject in a base64-encoded cursor.
        digest = hmac.new(self.key, b"guardoath-directory-owner:" + owner_sub.encode("ascii"), hashlib.sha256).digest()
        return _b64(digest)

    def _verify(self, cursor: str, search: str, limit: int, owner_sub: str) -> dict:
        if not isinstance(cursor, str) or len(cursor) > 4096 or cursor.count(".") != 1:
            raise InvalidCursor("Invalid pagination cursor")
        data, supplied = cursor.split(".")
        expected = _b64(hmac.new(self.key, data.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied, expected):
            raise InvalidCursor("Invalid pagination cursor")
        try:
            payload = json.loads(_decode(data))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidCursor("Invalid pagination cursor") from exc
        if (not isinstance(payload, dict) or payload.get("v") != 2 or
                payload.get("table") != self.table or payload.get("search") != search or
                payload.get("owner") != self._owner_label(owner_sub) or
                type(payload.get("limit")) is not int or payload["limit"] != limit or
                type(payload.get("exp")) is not int or payload["exp"] <= self.clock() or
                not isinstance(payload.get("key"), dict)):
            raise InvalidCursor("Invalid pagination cursor")
        key = payload["key"]
        if set(key) != {"tenant_id"} or not isinstance(key["tenant_id"], dict) or \
                set(key["tenant_id"]) != {"S"} or not isinstance(key["tenant_id"]["S"], str) or \
                not _TENANT.fullmatch(key["tenant_id"]["S"]):
            raise InvalidCursor("Invalid pagination cursor")
        return key

    def page(self, *, owner_sub: str, limit: int, search: str = "", cursor: str | None = None) -> dict:
        if not isinstance(owner_sub, str) or not OWNER_SUB.fullmatch(owner_sub):
            raise ValueError("Verified owner subject required for tenant directory")
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise ValueError("Invalid page limit")
        if not isinstance(search, str) or len(search) > 40 or \
                any(not (c.isascii() and (c.isalnum() or c in "-_")) for c in search):
            raise ValueError("Invalid search")
        request = {"TableName": self.table, "Limit": limit,
                   "ProjectionExpression": "tenant_id,#record,#revision,#usage",
                   "ExpressionAttributeNames": {"#record": "record", "#revision": "revision", "#usage": "usage"},
                   "ConsistentRead": True}
        if cursor is not None:
            request["ExclusiveStartKey"] = self._verify(cursor, search, limit, owner_sub)
        try:
            result = self.client.scan(**request)
            if (not isinstance(result, dict) or not isinstance(result.get("Items"), list)
                    or len(result["Items"]) > limit):
                raise DirectoryUnavailable("Malformed tenant directory response")
            month = datetime.now(timezone.utc).strftime("%Y-%m")
            projected = [_project_item(item, month) for item in result["Items"]]
            identifiers = [entry["tenant_id"] for entry in projected]
            if len(set(identifiers)) != len(identifiers):
                raise DirectoryUnavailable("Duplicate tenant directory identity")
            tenants = [entry for entry in projected
                       if not search or search.casefold() in entry["tenant_id"].casefold()]
            last = result.get("LastEvaluatedKey")
            next_cursor = None
            if last is not None:
                if (not isinstance(last, dict) or set(last) != {"tenant_id"}
                        or not isinstance(last["tenant_id"], dict)
                        or set(last["tenant_id"]) != {"S"}
                        or not isinstance(last["tenant_id"]["S"], str)
                        or not _TENANT.fullmatch(last["tenant_id"]["S"])):
                    raise DirectoryUnavailable("Malformed tenant scan checkpoint")
                next_cursor = self._sign({"v": 2, "table": self.table, "search": search,
                                          "owner": self._owner_label(owner_sub), "limit": limit,
                                          "key": last, "exp": int(self.clock()) + CURSOR_TTL_SECONDS})
        except DirectoryUnavailable:
            raise
        except Exception as exc:
            raise DirectoryUnavailable("Tenant directory source unavailable") from exc
        return {"status": "ok", "source": "DynamoDB tenant table", "tenants": tenants,
                "returned": len(tenants), "scanned": len(result["Items"]),
                "total": None, "next_cursor": next_cursor, "limit": limit,
                "note": "Scanned only one bounded page; total count unavailable"}
