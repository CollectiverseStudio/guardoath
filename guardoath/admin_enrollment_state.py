"""Signed, metadata-only first-enrollment control in the independent audit table.

These explicitly namespaced control rows are mutable by full-document CAS.
They are not audit receipts; existing signed audit receipts remain immutable.
No password, OTP, token, provider session, email or setup key is persisted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re

from guardoath.admin_cognito import SUBJECT


class EnrollmentStateUnavailable(Exception):
    """Persistence is unavailable or ambiguous; no provider write is authorized."""


def provider_code(exc) -> str | None:
    response = getattr(exc, "response", None)
    error = response.get("Error") if isinstance(response, dict) else None
    code = error.get("Code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None


class DynamoEnrollmentState:
    """One durable control record per owner/pool/client; no blind overwrites."""

    MAX_AGE = 180
    MAX_ATTEMPTS = 5
    KIND = "guardoath-owner-totp-enrollment-control-v1"
    FIELDS = {"version", "kind", "subject", "nonce", "issued", "expires",
              "state", "phase", "attempts", "revision", "signature"}

    def __init__(self, client, table: str, key: bytes, pool_id: str, client_id: str):
        if (not isinstance(key, bytes) or len(key) < 32 or not isinstance(table, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", table)
                or not pool_id or not client_id or not all(callable(getattr(client, name, None))
                                                         for name in ("get_item", "put_item"))):
            raise ValueError("Independent enrollment persistence is required")
        self._client, self.table_name = client, table
        self.pool_id, self.client_id = pool_id, client_id
        context = (self.KIND + "\0" + pool_id + "\0" + client_id).encode("utf-8")
        self._key = hmac.new(key, context, hashlib.sha256).digest()

    @staticmethod
    def _json(value) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def _id(self, subject: str) -> str:
        if not isinstance(subject, str) or not SUBJECT.fullmatch(subject):
            raise EnrollmentStateUnavailable() from None
        return "enrollment-control:" + hmac.new(
            self._key, b"control-id\0" + subject.encode("ascii"), hashlib.sha256).hexdigest()

    def _signed(self, value: dict) -> dict:
        unsigned = {k: v for k, v in value.items() if k != "signature"}
        return {**unsigned, "signature": hmac.new(
            self._key, b"control-document\0" + self._json(unsigned).encode("ascii"),
            hashlib.sha256).hexdigest()}

    def read(self, subject: str) -> dict | None:
        event_id = self._id(subject)
        try:
            response = self._client.get_item(TableName=self.table_name,
                                            Key={"event_id": {"S": event_id}}, ConsistentRead=True)
        except Exception:
            raise EnrollmentStateUnavailable() from None
        if not isinstance(response, dict):
            raise EnrollmentStateUnavailable() from None
        if "Item" not in response:
            return None
        item = response["Item"]
        if (not isinstance(item, dict) or set(item) != {"event_id", "enrollment_control"}
                or item.get("event_id") != {"S": event_id}
                or not isinstance(item.get("enrollment_control"), dict)
                or set(item["enrollment_control"]) != {"S"}):
            raise EnrollmentStateUnavailable() from None
        raw = item["enrollment_control"]["S"]
        try:
            value = json.loads(raw) if isinstance(raw, str) and len(raw) <= 2048 else None
            valid = (isinstance(value, dict) and set(value) == self.FIELDS
                     and type(value["version"]) is int and value["version"] == 1
                     and value["kind"] == self.KIND and value["subject"] == subject
                     and isinstance(value["nonce"], str) and re.fullmatch(r"[a-f0-9]{32}", value["nonce"])
                     and type(value["issued"]) is int and value["issued"] > 0
                     and type(value["expires"]) is int
                     and value["expires"] == value["issued"] + self.MAX_AGE
                     and type(value["revision"]) is int and 1 <= value["revision"] <= 128
                     and type(value["attempts"]) is int and 0 <= value["attempts"] <= self.MAX_ATTEMPTS
                     and value["state"] in ("PENDING", "VERIFYING", "COMPLETE", "CANCELLED", "BLOCKED")
                     and value["phase"] in ("ASSOCIATING", "READY")
                     and (value["phase"] == "READY" or value["state"] in ("PENDING", "BLOCKED"))
                     and isinstance(value["signature"], str)
                     and hmac.compare_digest(value["signature"], self._signed(value)["signature"])
                     and self._json(value) == raw)
        except (ValueError, TypeError, KeyError, UnicodeError):
            valid = False
        if not valid:
            raise EnrollmentStateUnavailable() from None
        return value

    def _put(self, value: dict, previous: dict | None) -> dict | None:
        signed = self._signed(value)
        params = {
            "TableName": self.table_name,
            "Item": {"event_id": {"S": self._id(signed["subject"])},
                     "enrollment_control": {"S": self._json(signed)}},
        }
        if previous is None:
            params["ConditionExpression"] = "attribute_not_exists(event_id)"
        else:
            # The entire previously verified document, not just its revision,
            # binds the nonce, signature, state, phase, owner and expiry.
            params.update(ConditionExpression="attribute_exists(event_id) AND #control = :original",
                          ExpressionAttributeNames={"#control": "enrollment_control"},
                          ExpressionAttributeValues={":original": {"S": self._json(previous)}})
        try:
            response = self._client.put_item(**params)
        except Exception as exc:
            if provider_code(exc) == "ConditionalCheckFailedException":
                return None
            raise EnrollmentStateUnavailable() from None
        if not isinstance(response, dict):
            raise EnrollmentStateUnavailable() from None
        return signed

    def reserve(self, subject: str, nonce: str, now: int) -> dict | None:
        if (not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{32}", nonce)
                or type(now) is not int or now <= 0):
            raise EnrollmentStateUnavailable() from None
        old = self.read(subject)
        if old is not None and not (old["state"] == "CANCELLED" or (
                old["state"] == "PENDING" and old["phase"] == "READY" and old["expires"] < now)):
            return None
        return self._put({"version": 1, "kind": self.KIND, "subject": subject, "nonce": nonce,
                          "issued": now, "expires": now + self.MAX_AGE, "state": "PENDING",
                          "phase": "ASSOCIATING", "attempts": 0, "revision": 1}, old)

    def ready(self, record: dict) -> dict | None:
        if record["state"] != "PENDING" or record["phase"] != "ASSOCIATING":
            return None
        return self._put({**record, "phase": "READY", "revision": record["revision"] + 1}, record)

    def current(self, subject: str, nonce: str, issued: int, now: int) -> dict | None:
        record = self.read(subject)
        if (record is None or record["nonce"] != nonce or record["issued"] != issued
                or record["state"] != "PENDING" or record["phase"] != "READY"
                or now < record["issued"] - 30 or now > record["expires"]):
            return None
        return record

    def verifying(self, record: dict) -> dict | None:
        if (record["state"] != "PENDING" or record["phase"] != "READY"
                or record["attempts"] >= self.MAX_ATTEMPTS):
            return None
        return self._put({**record, "state": "VERIFYING", "attempts": record["attempts"] + 1,
                          "revision": record["revision"] + 1}, record)

    def retry_code(self, record: dict, now: int) -> dict | None:
        if record["state"] != "VERIFYING" or now > record["expires"] or record["revision"] >= 120:
            return None
        return self._put({**record, "state": "PENDING", "revision": record["revision"] + 1}, record)

    def complete(self, record: dict) -> dict | None:
        if record["state"] != "VERIFYING":
            return None
        return self._put({**record, "state": "COMPLETE", "revision": record["revision"] + 1}, record)

    def cancel(self, record: dict) -> dict | None:
        if record["state"] != "PENDING" or record["phase"] != "READY":
            return None
        return self._put({**record, "state": "CANCELLED", "revision": record["revision"] + 1}, record)

    def block(self, record: dict) -> dict | None:
        """Best-effort terminal marking; ASSOCIATING/VERIFYING never auto-expire."""
        return self._put({**record, "state": "BLOCKED", "revision": record["revision"] + 1}, record)
