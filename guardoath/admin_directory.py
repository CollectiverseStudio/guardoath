"""Bounded, read-only DynamoDB tenant-directory page for GuardOath administrators.

Does not replace the tenant record, and never returns full raw DynamoDB items.
Uses opaque authenticated cursors so callers cannot submit arbitrary scan keys.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Callable

MAX_PAGE = 50
CURSOR_TTL_SECONDS = 900


class InvalidCursor(ValueError):
    pass


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

    def _verify(self, cursor: str, search: str) -> dict:
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
        if (not isinstance(payload, dict) or payload.get("v") != 1 or
                payload.get("table") != self.table or payload.get("search") != search or
                type(payload.get("exp")) is not int or payload["exp"] <= self.clock() or
                not isinstance(payload.get("key"), dict)):
            raise InvalidCursor("Invalid pagination cursor")
        key = payload["key"]
        if set(key) != {"tenant_id"} or not isinstance(key["tenant_id"], dict) or \
                set(key["tenant_id"]) != {"S"} or not isinstance(key["tenant_id"]["S"], str):
            raise InvalidCursor("Invalid pagination cursor")
        return key

    def page(self, *, limit: int, search: str = "", cursor: str | None = None) -> dict:
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise ValueError("Invalid page limit")
        if not isinstance(search, str) or len(search) > 40 or \
                any(not (c.isascii() and (c.isalnum() or c in "-_")) for c in search):
            raise ValueError("Invalid search")
        request = {"TableName": self.table, "Limit": limit,
                   "ProjectionExpression": "tenant_id,#record,#revision,#usage",
                   "ExpressionAttributeNames": {"#record": "record", "#revision": "revision", "#usage": "usage"},
                   "ConsistentRead": True}
        if cursor:
            request["ExclusiveStartKey"] = self._verify(cursor, search)
        result = self.client.scan(**request)
        if not isinstance(result, dict) or not isinstance(result.get("Items"), list) or len(result["Items"]) > limit:
            raise ValueError("Invalid DynamoDB scan response")
        tenants = []
        for item in result["Items"]:
            tenant_id = item.get("tenant_id", {}).get("S")
            if not isinstance(tenant_id, str) or (search and search.casefold() not in tenant_id.casefold()):
                continue
            # Full tenant document is parsed only server-side; explicit keys below.
            doc = json.loads(item["record"]["S"])
            if not isinstance(doc, dict) or doc.get("tenant_id") != tenant_id:
                raise ValueError("Invalid tenant record")
            month = datetime.now(timezone.utc).strftime("%Y-%m")
            month_usage = item.get("usage", {}).get("M", {}).get(month)
            usage_count = None
            if month_usage is not None:
                raw_usage = month_usage.get("N") if isinstance(month_usage, dict) else None
                if not isinstance(raw_usage, str) or not raw_usage.isdigit():
                    raise ValueError("Invalid DynamoDB usage record")
                usage_count = int(raw_usage)
            tenants.append({
                "tenant_id": tenant_id,
                "display_name": str(doc.get("display_name", ""))[:160],
                "status": str(doc.get("status", "unknown")),
                "demo": doc.get("demo") is True,
                "subscription_status": str(doc.get("subscription_status", "unknown")),
                "monthly_allowance": int(doc.get("monthly_allowance", 0)),
                # Metadata only; an absent or malformed legacy key list is unknown,
                # not an invented zero and never a leak of stored key hashes.
                "api_key_count": (len(doc["key_hashes"])
                                  if isinstance(doc.get("key_hashes"), list)
                                  and len(doc["key_hashes"]) <= 2
                                  and all(isinstance(key, str) for key in doc["key_hashes"])
                                  else None),
                "usage_month": month,
                "usage_month_count": usage_count,
                "subscription_source": "persisted tenant record; not live Stripe verification",
                "revision": int(item["revision"]["N"]),
                "updated_at": str(doc.get("updated_at", "")),
            })
        last = result.get("LastEvaluatedKey")
        next_cursor = None
        if last:
            # Never accept a noncanonical cursor key from an unexpected table.
            if set(last) != {"tenant_id"} or not isinstance(last["tenant_id"].get("S"), str):
                raise ValueError("Invalid scan checkpoint")
            next_cursor = self._sign({"v": 1, "table": self.table, "search": search,
                                      "key": last, "exp": int(self.clock()) + CURSOR_TTL_SECONDS})
        return {"status": "ok", "source": "DynamoDB tenant table", "tenants": tenants,
                "returned": len(tenants), "scanned": len(result["Items"]),
                "total": None, "next_cursor": next_cursor, "limit": limit,
                "note": "Scanned only one bounded page; total count unavailable"}
