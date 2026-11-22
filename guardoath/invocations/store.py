"""Metadata-only invocation receipts, atomic dispatch admission and held quotas.

SQLite is for a shared local file, not independent Lambda filesystems. DynamoDB
requires a dedicated pk/sk table and an injected, deadline-bounded client. No
constructor creates AWS resources or discovers credentials. A failed/ambiguous
write never returns dispatch authority, even if the write might have committed.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import os
import re
import sqlite3
from uuid import uuid4

from .contracts import InvocationReceipt, TERMINAL_STATUSES
from guardoath.models import Action


ZERO_DIGEST = "0" * 64
MAX_DOCUMENT_BYTES = 65_536
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
_PROVIDER_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,1023}")
_MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
_TRANSITIONS = {
    "admitted": {"input_checked", "unavailable", "quota_exceeded"},
    "input_checked": {"dispatch_intent", "input_blocked", "input_incomplete", "unavailable", "quota_exceeded"},
    "dispatch_intent": {"provider_result", "outcome_unknown", "provider_rejected"},
    "provider_result": {"completed", "provider_withheld", "provider_rejected", "output_blocked",
                        "output_incomplete", "unavailable", "outcome_unknown"},
}
_IMMUTABLE = ("schema_version", "application_id", "interaction_id", "request_id", "created_at",
              "target", "policy_revision", "policy_digest", "catalog_version", "response_retained")
_OBSERVATIONS = ("provider_request_id", "provider_response_id", "observed_model", "observed_service_tier", "usage")


class InvocationConflict(Exception):
    def __init__(self, *unused):
        super().__init__("Invocation request or state conflicts with the durable receipt")


class InvocationQuotaExceeded(Exception):
    def __init__(self, *unused):
        super().__init__("Invocation allowance is exhausted")


class InvocationStoreUnavailable(Exception):
    def __init__(self, *unused):
        super().__init__("Invocation persistence could not be confirmed")


class _WriteConflict(Exception):
    """A transaction definitively failed a condition, without committing."""


@dataclass(frozen=True)
class AdmissionResult:
    receipt: InvocationReceipt
    created: bool


@dataclass(frozen=True)
class DispatchQuota:
    monthly_call_limit: int
    monthly_output_token_limit: int
    period: str

    def __post_init__(self):
        if (type(self.monthly_call_limit) is not int or not 1 <= self.monthly_call_limit <= 1_000_000
                or type(self.monthly_output_token_limit) is not int
                or not 1 <= self.monthly_output_token_limit <= 1_000_000_000
                or not isinstance(self.period, str) or not _MONTH.fullmatch(self.period)
                or self.period.startswith("0000-")):
            raise ValueError("Invalid trusted invocation quota")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _application(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise InvocationConflict()
    return value


def _interaction(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise InvocationConflict()
    return value


def _fingerprint(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise InvocationConflict()
    return value


def _receipt(value):
    try:
        if not isinstance(value, InvocationReceipt):
            raise ValueError
        # Revalidate copies/model_construct; frozen Pydantic copies can contain
        # unvalidated updates. There is no generic metadata/error/content bag.
        clean = InvocationReceipt.model_validate(value.model_dump(mode="json"))
        _application(clean.application_id)
        for stamp in (clean.created_at, clean.updated_at):
            datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
        if clean.updated_at < clean.created_at:
            raise ValueError
        for check in (clean.input_check, clean.output_check):
            if check is not None and not _IDENTIFIER.fullmatch(check.check_id):
                raise ValueError
        if not _IDENTIFIER.fullmatch(clean.catalog_version):
            raise ValueError
        for identifier in (clean.target.provider_model_id, clean.provider_request_id,
                           clean.provider_response_id, clean.observed_model):
            if identifier is not None and not _PROVIDER_IDENTIFIER.fullmatch(identifier):
                raise ValueError
        if (clean.target.region is not None and
                not re.fullmatch(r"[a-z0-9-]{1,40}", clean.target.region)):
            raise ValueError
        if (clean.observed_service_tier is not None and
                not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", clean.observed_service_tier)):
            raise ValueError
        if clean.quota_period is not None and not _MONTH.fullmatch(clean.quota_period):
            raise ValueError
        if len(_canonical(clean.model_dump(mode="json")).encode("ascii")) > MAX_DOCUMENT_BYTES - 1024:
            raise ValueError
        return clean
    except Exception:
        raise InvocationConflict() from None


def _approved(evidence):
    return (evidence is not None and evidence.evaluation_status == "complete"
            and not evidence.simulated and evidence.decision != Action.BLOCK)


def _initial(receipt):
    if (receipt.status != "admitted" or receipt.dispatch_state != "not_dispatched"
            or receipt.sequence != 0 or receipt.event_digest != ZERO_DIGEST
            or receipt.reason_code is not None or receipt.input_check is not None
            or receipt.output_check is not None or receipt.reserved_output_tokens != 0
            or receipt.quota_period is not None
            or any(getattr(receipt, name) is not None for name in _OBSERVATIONS)):
        raise InvocationConflict()


def _transition(previous, following, *, reserve=False, signed=False):
    if (previous.status in TERMINAL_STATUSES
            or following.status not in _TRANSITIONS.get(previous.status, set())
            or any(getattr(previous, field) != getattr(following, field) for field in _IMMUTABLE)
            or following.updated_at < previous.updated_at
            or following.sequence != previous.sequence + (1 if signed else 0)
            or (not signed and following.event_digest != previous.event_digest)):
        raise InvocationConflict()
    if previous.input_check is not None and following.input_check != previous.input_check:
        raise InvocationConflict()
    if previous.status != "admitted" and following.input_check is None:
        raise InvocationConflict()
    if previous.status == "admitted" and following.status != "input_checked" and following.input_check is not None:
        raise InvocationConflict()
    if following.status == "input_checked" and following.input_check is None:
        raise InvocationConflict()
    if following.status not in {"completed", "output_blocked", "output_incomplete"} and following.output_check is not None:
        raise InvocationConflict()
    if following.status == "input_blocked" and not (
            following.input_check is not None and following.input_check.decision == Action.BLOCK
            and following.input_check.evaluation_status == "complete" and not following.input_check.simulated):
        raise InvocationConflict()
    if following.status == "output_blocked" and not (
            following.output_check is not None and following.output_check.decision == Action.BLOCK
            and following.output_check.evaluation_status == "complete" and not following.output_check.simulated):
        raise InvocationConflict()
    if following.status == "completed" and not (_approved(following.input_check) and _approved(following.output_check)):
        raise InvocationConflict()
    if following.status not in TERMINAL_STATUSES and following.reason_code is not None:
        raise InvocationConflict()
    if following.status == "dispatch_intent":
        if (not reserve or not _approved(following.input_check)
                or following.dispatch_state != "intent_recorded"
                or not 1 <= following.reserved_output_tokens <= 4096
                or following.quota_period != following.updated_at[:7]):
            raise InvocationConflict()
    else:
        if (reserve or following.reserved_output_tokens != previous.reserved_output_tokens
                or following.quota_period != previous.quota_period):
            raise InvocationConflict()
    local_refusal = previous.status == "dispatch_intent" and following.status == "provider_rejected"
    if local_refusal and (
            following.reason_code not in {"request_invalid", "model_not_allowed", "configuration_unavailable"}
            or any(getattr(following, field) is not None for field in _OBSERVATIONS)
            or not _approved(following.input_check)):
        raise InvocationConflict()
    expected_dispatch = ("not_dispatched" if local_refusal else
                         "outcome_unknown" if following.status == "outcome_unknown" else
                         "intent_recorded" if following.status == "dispatch_intent" else
                         "response_observed" if following.status in {
                             "provider_result", "completed", "provider_withheld", "provider_rejected",
                             "output_blocked", "output_incomplete"} else previous.dispatch_state)
    if following.dispatch_state != expected_dispatch:
        raise InvocationConflict()
    first_unknown = previous.status == "dispatch_intent" and following.status == "outcome_unknown"
    if following.status != "provider_result" and not first_unknown and any(
            getattr(following, field) != getattr(previous, field) for field in _OBSERVATIONS):
        raise InvocationConflict()


class _InvocationStore:
    def __init__(self, signing_key):
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("Invocation persistence requires a stable signing key of at least 32 bytes")
        self._keys = {purpose: hmac.new(signing_key, ("guardoath-invocations-v1/" + purpose).encode("ascii"),
                                        hashlib.sha256).digest()
                      for purpose in ("request", "request-id", "event", "mapping", "index", "budget")}

    def _mac(self, purpose, value):
        return hmac.new(self._keys[purpose], _canonical(value).encode("ascii"), hashlib.sha256).hexdigest()

    def make_fingerprint(self, application_id, canonical_request_bytes):
        """One-way request binding; the bytes are never written or logged."""
        _application(application_id)
        if not isinstance(canonical_request_bytes, bytes) or not 1 <= len(canonical_request_bytes) <= 131_072:
            raise InvocationConflict()
        tenant = application_id.encode("ascii")
        return hmac.new(self._keys["request"], len(tenant).to_bytes(2, "big") + tenant + canonical_request_bytes,
                        hashlib.sha256).hexdigest()

    @staticmethod
    def _pk(application_id):
        return "T#" + _application(application_id)

    def _request_key(self, application_id, request_id):
        return "R#" + self._mac("request-id", [application_id, request_id])

    def _signed(self, purpose, value):
        return {**value, "signature": self._mac(purpose, value)}

    def _document(self, raw, fields):
        try:
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_DOCUMENT_BYTES:
                raise ValueError
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != set(fields) or _canonical(value) != raw:
                raise ValueError
            return value
        except Exception:
            raise InvocationStoreUnavailable() from None

    def _verified(self, raw, purpose, fields):
        value = self._document(raw, {*fields, "signature"})
        signature = value.pop("signature")
        if (not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature)
                or not hmac.compare_digest(signature, self._mac(purpose, value))):
            raise InvocationStoreUnavailable()
        return value

    def _seal_event(self, receipt, fingerprint, previous_digest):
        value = receipt.model_dump(mode="json", exclude={"event_digest"})
        value["sequence"] += 1
        digest = self._mac("event", {"fingerprint": fingerprint, "previous_digest": previous_digest, "receipt": value})
        signed = InvocationReceipt.model_validate({**value, "event_digest": digest})
        event = _canonical({"fingerprint": fingerprint, "previous_digest": previous_digest,
                            "receipt": signed.model_dump(mode="json")})
        head = _canonical({"fingerprint": fingerprint, "receipt": signed.model_dump(mode="json")})
        return signed, head, event

    def _decode_receipt(self, value):
        try:
            return _receipt(InvocationReceipt.model_validate(value))
        except Exception:
            raise InvocationStoreUnavailable() from None

    def _mapping(self, application_id, request_id):
        raw = self._read(self._pk(application_id), self._request_key(application_id, request_id))
        if raw is None:
            return None
        value = self._verified(raw, "mapping", {"application_id", "request_id", "interaction_id", "fingerprint"})
        try:
            _interaction(value["interaction_id"])
            _fingerprint(value["fingerprint"])
            if value["application_id"] != application_id or value["request_id"] != request_id:
                raise ValueError
        except Exception:
            raise InvocationStoreUnavailable() from None
        return value

    def _load(self, application_id, interaction_id):
        pk = self._pk(application_id)
        _interaction(interaction_id)
        head_raw = self._read(pk, "I#" + interaction_id)
        if head_raw is None:
            return None
        head = self._document(head_raw, {"fingerprint", "receipt"})
        receipt = self._decode_receipt(head["receipt"])
        try:
            _fingerprint(head["fingerprint"])
            if (receipt.application_id != application_id or receipt.interaction_id != interaction_id
                    or not 1 <= receipt.sequence <= 20):
                raise ValueError
            rows = self._select(pk, "E#" + interaction_id + "#00001",
                                "E#" + interaction_id + f"#{receipt.sequence:05}", receipt.sequence)
            if len(rows) != receipt.sequence:
                raise ValueError
            previous, previous_digest = None, ZERO_DIGEST
            for sequence, (key, raw) in enumerate(rows, 1):
                event = self._document(raw, {"fingerprint", "previous_digest", "receipt"})
                current = self._decode_receipt(event["receipt"])
                if (key != "E#" + interaction_id + f"#{sequence:05}"
                        or current.sequence != sequence or current.application_id != application_id
                        or current.interaction_id != interaction_id or event["fingerprint"] != head["fingerprint"]
                        or event["previous_digest"] != previous_digest):
                    raise ValueError
                expected = self._mac("event", {"fingerprint": event["fingerprint"],
                    "previous_digest": event["previous_digest"],
                    "receipt": current.model_dump(mode="json", exclude={"event_digest"})})
                if not hmac.compare_digest(current.event_digest, expected):
                    raise ValueError
                if previous is None:
                    _initial(current.model_copy(update={"sequence": 0, "event_digest": ZERO_DIGEST}))
                else:
                    _transition(previous, current, reserve=current.status == "dispatch_intent", signed=True)
                previous, previous_digest = current, current.event_digest
            if previous != receipt:
                raise ValueError
            mapping = self._mapping(application_id, receipt.request_id)
            if (mapping is None or mapping["interaction_id"] != interaction_id
                    or mapping["fingerprint"] != head["fingerprint"]):
                raise ValueError
        except Exception:
            raise InvocationStoreUnavailable() from None
        return receipt, head_raw, head["fingerprint"]

    def get(self, application_id, interaction_id):
        loaded = self._load(application_id, interaction_id)
        return loaded[0] if loaded is not None else None

    def lookup_request(self, application_id, request_id):
        _application(application_id)
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", request_id):
            raise InvocationConflict()
        mapping = self._mapping(application_id, request_id)
        if mapping is None:
            return None
        receipt = self.get(application_id, mapping["interaction_id"])
        if receipt is None or receipt.request_id != request_id:
            raise InvocationStoreUnavailable()
        return receipt

    def recent(self, application_id, limit=20):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise InvocationConflict()
        rows = self._select(self._pk(application_id), "L#", "L#~", limit, descending=True)
        receipts = []
        for key, raw in rows:
            value = self._verified(raw, "index", {"application_id", "interaction_id", "created_at"})
            if (value["application_id"] != application_id or not isinstance(value["interaction_id"], str)
                    or not isinstance(value["created_at"], str)
                    or key != "L#" + value["created_at"] + "#" + value["interaction_id"]):
                raise InvocationStoreUnavailable()
            try:
                receipt = self.get(application_id, value["interaction_id"])
            except InvocationConflict:
                raise InvocationStoreUnavailable() from None
            if receipt is None or receipt.created_at != value["created_at"]:
                raise InvocationStoreUnavailable()
            receipts.append(receipt)
        return tuple(receipts)

    def _duplicate(self, receipt, fingerprint):
        mapping = self._mapping(receipt.application_id, receipt.request_id)
        if mapping is None:
            return None
        if not hmac.compare_digest(mapping["fingerprint"], fingerprint):
            raise InvocationConflict()
        previous = self.get(receipt.application_id, mapping["interaction_id"])
        if previous is None:
            raise InvocationStoreUnavailable()
        return AdmissionResult(previous, False)

    def admit(self, receipt, fingerprint):
        receipt, fingerprint = _receipt(receipt), _fingerprint(fingerprint)
        _initial(receipt)
        duplicate = self._duplicate(receipt, fingerprint)
        if duplicate is not None:
            return duplicate
        signed, head, event = self._seal_event(receipt, fingerprint, ZERO_DIGEST)
        pk = self._pk(receipt.application_id)
        mapping = self._signed("mapping", {"application_id": receipt.application_id, "request_id": receipt.request_id,
            "interaction_id": receipt.interaction_id, "fingerprint": fingerprint})
        index = self._signed("index", {"application_id": receipt.application_id, "interaction_id": receipt.interaction_id,
                                       "created_at": receipt.created_at})
        try:
            self._write([
                (pk, self._request_key(receipt.application_id, receipt.request_id), _canonical(mapping), None),
                (pk, "I#" + receipt.interaction_id, head, None),
                (pk, "E#" + receipt.interaction_id + "#00001", event, None),
                (pk, "L#" + receipt.created_at + "#" + receipt.interaction_id, _canonical(index), None),
            ])
        except _WriteConflict:
            duplicate = self._duplicate(receipt, fingerprint)
            if duplicate is None:
                raise InvocationConflict() from None
            return duplicate
        return AdmissionResult(signed, True)

    def _advance_rows(self, expected_receipt, next_receipt, *, reserve=False):
        expected, following = _receipt(expected_receipt), _receipt(next_receipt)
        _transition(expected, following, reserve=reserve)
        loaded = self._load(expected.application_id, expected.interaction_id)
        if loaded is None or loaded[0] != expected:
            raise InvocationConflict()
        current, raw, fingerprint = loaded
        signed, head, event = self._seal_event(following, fingerprint, current.event_digest)
        pk = self._pk(current.application_id)
        return signed, [
            (pk, "I#" + current.interaction_id, head, raw),
            (pk, "E#" + current.interaction_id + f"#{signed.sequence:05}", event, None),
        ]

    def advance(self, expected_receipt, next_receipt):
        signed, rows = self._advance_rows(expected_receipt, next_receipt)
        try:
            self._write(rows)
        except _WriteConflict:
            raise InvocationConflict() from None
        return signed

    def _budget(self, application_id, connection_id, period):
        pk, sk = self._pk(application_id), "B#" + connection_id + "#" + period
        raw = self._read(pk, sk)
        if raw is None:
            value = {"application_id": application_id, "connection_id": connection_id, "period": period,
                     "calls": 0, "output_tokens": 0, "revision": 0}
        else:
            value = self._verified(raw, "budget", {"application_id", "connection_id", "period", "calls",
                                                  "output_tokens", "revision"})
            if (value["application_id"] != application_id or value["connection_id"] != connection_id
                    or value["period"] != period or type(value["calls"]) is not int
                    or not 1 <= value["calls"] <= 1_000_000
                    or type(value["output_tokens"]) is not int or not 1 <= value["output_tokens"] <= 1_000_000_000
                    or type(value["revision"]) is not int or value["revision"] != value["calls"]):
                raise InvocationStoreUnavailable()
        return pk, sk, raw, value

    def reserve_dispatch(self, expected_receipt, next_receipt, quota):
        if not isinstance(quota, DispatchQuota):
            raise InvocationConflict()
        # Recheck even if a caller deliberately bypassed the frozen constructor.
        try:
            DispatchQuota(quota.monthly_call_limit, quota.monthly_output_token_limit, quota.period)
        except Exception:
            raise InvocationConflict() from None
        next_receipt = _receipt(next_receipt)
        if next_receipt.quota_period != quota.period:
            raise InvocationConflict()
        # Retry only a *confirmed rejected* CAS. Unknown/network results escape
        # immediately. The same expected head can never authorize another call.
        for _ in range(8):
            signed, rows = self._advance_rows(expected_receipt, next_receipt, reserve=True)
            pk, sk, raw, budget = self._budget(signed.application_id, signed.target.connection_id, quota.period)
            if (budget["calls"] >= quota.monthly_call_limit or
                    budget["output_tokens"] + signed.reserved_output_tokens > quota.monthly_output_token_limit):
                raise InvocationQuotaExceeded()
            updated = self._signed("budget", {**budget, "calls": budget["calls"] + 1,
                "output_tokens": budget["output_tokens"] + signed.reserved_output_tokens,
                "revision": budget["revision"] + 1})
            try:
                self._write([*rows, (pk, sk, _canonical(updated), raw)])
            except _WriteConflict:
                continue
            return signed
        raise InvocationStoreUnavailable()


class SQLiteInvocationStore(_InvocationStore):
    """Durable local transactions; multiple instances share one SQLite file."""

    def __init__(self, path, signing_key):
        super().__init__(signing_key)
        try:
            self.path = os.fspath(path)
            if not isinstance(self.path, str) or not self.path or self.path == ":memory:":
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("Invocation persistence requires a local database file") from None
        try:
            with closing(self._connect()) as database:
                database.execute("PRAGMA journal_mode=WAL")
                database.execute("CREATE TABLE IF NOT EXISTS invocation_items ("
                    "pk TEXT NOT NULL, sk TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY (pk, sk))")
        except sqlite3.Error:
            raise InvocationStoreUnavailable() from None

    def _connect(self):
        database = sqlite3.connect(self.path, timeout=2.0, isolation_level=None)
        database.execute("PRAGMA synchronous=FULL")
        return database

    def _read(self, pk, sk):
        try:
            with closing(self._connect()) as database:
                row = database.execute("SELECT document FROM invocation_items WHERE pk=? AND sk=?", (pk, sk)).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            raise InvocationStoreUnavailable() from None

    def _select(self, pk, start, end, limit, descending=False):
        try:
            with closing(self._connect()) as database:
                return database.execute("SELECT sk, document FROM invocation_items WHERE pk=? AND sk BETWEEN ? AND ? "
                    + ("ORDER BY sk DESC LIMIT ?" if descending else "ORDER BY sk ASC LIMIT ?"),
                    (pk, start, end, limit)).fetchall()
        except sqlite3.Error:
            raise InvocationStoreUnavailable() from None

    def _write(self, changes):
        database = None
        try:
            database = self._connect()
            database.execute("BEGIN IMMEDIATE")
            for pk, sk, document, previous in changes:
                row = database.execute("SELECT document FROM invocation_items WHERE pk=? AND sk=?", (pk, sk)).fetchone()
                if (None if row is None else row[0]) != previous:
                    raise _WriteConflict()
            for pk, sk, document, previous in changes:
                if previous is None:
                    database.execute("INSERT INTO invocation_items (pk, sk, document) VALUES (?, ?, ?)", (pk, sk, document))
                else:
                    database.execute("UPDATE invocation_items SET document=? WHERE pk=? AND sk=?", (document, pk, sk))
            database.execute("COMMIT")
        except _WriteConflict:
            raise
        except sqlite3.Error:
            raise InvocationStoreUnavailable() from None
        finally:
            if database is not None:
                try:
                    if database.in_transaction:
                        database.rollback()
                    database.close()
                except sqlite3.Error:
                    raise InvocationStoreUnavailable() from None


class DynamoDBInvocationStore(_InvocationStore):
    """Dedicated metadata table; all writes are atomic, conditional transactions."""

    def __init__(self, table_name, client, signing_key):
        super().__init__(signing_key)
        if (not isinstance(table_name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", table_name)
                or not all(callable(getattr(client, name, None)) for name in
                           ("get_item", "query", "transact_write_items"))):
            raise ValueError("A dedicated invocation table and bounded DynamoDB client are required")
        self.table_name, self.client = table_name, client

    @staticmethod
    def _item(item, pk, sk=None):
        try:
            if (not isinstance(item, dict) or set(item) != {"pk", "sk", "document"}
                    or item["pk"] != {"S": pk} or set(item["sk"]) != {"S"}
                    or not isinstance(item["sk"]["S"], str) or set(item["document"]) != {"S"}
                    or not isinstance(item["document"]["S"], str)
                    or (sk is not None and item["sk"]["S"] != sk)):
                raise ValueError
            return item["sk"]["S"], item["document"]["S"]
        except Exception:
            raise InvocationStoreUnavailable() from None

    def _read(self, pk, sk):
        try:
            response = self.client.get_item(TableName=self.table_name,
                Key={"pk": {"S": pk}, "sk": {"S": sk}}, ConsistentRead=True)
            if not isinstance(response, dict):
                raise ValueError
            return self._item(response["Item"], pk, sk)[1] if "Item" in response else None
        except Exception:
            raise InvocationStoreUnavailable() from None

    def _select(self, pk, start, end, limit, descending=False):
        params = {"TableName": self.table_name, "ConsistentRead": True, "ScanIndexForward": not descending,
                  "KeyConditionExpression": "#pk = :pk AND #sk BETWEEN :start AND :end",
                  "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"},
                  "ExpressionAttributeValues": {":pk": {"S": pk}, ":start": {"S": start}, ":end": {"S": end}}}
        rows, cursors = [], set()
        try:
            for _ in range(21):
                response = self.client.query(**params, Limit=limit - len(rows))
                if not isinstance(response, dict) or not isinstance(response.get("Items"), list):
                    raise ValueError
                page = [self._item(item, pk) for item in response["Items"]]
                if len(page) > limit - len(rows) or any(not start <= key <= end for key, _ in page):
                    raise ValueError
                rows.extend(page)
                keys = [key for key, _ in rows]
                if keys != sorted(set(keys), reverse=descending):
                    raise ValueError
                if len(rows) == limit or "LastEvaluatedKey" not in response:
                    return rows
                cursor = response["LastEvaluatedKey"]
                if cursor == {}:
                    return rows
                if not isinstance(cursor, dict) or set(cursor) != {"pk", "sk"} or cursor["pk"] != {"S": pk}:
                    raise ValueError
                if not isinstance(cursor["sk"], dict) or set(cursor["sk"]) != {"S"}:
                    raise ValueError
                position = cursor["sk"]["S"]
                if (not isinstance(position, str) or not start <= position <= end or position in cursors
                        or (page and position != page[-1][0])):
                    raise ValueError
                cursors.add(position)
                params["ExclusiveStartKey"] = cursor
            raise ValueError
        except Exception:
            raise InvocationStoreUnavailable() from None

    def _write(self, changes):
        items = []
        for pk, sk, document, previous in changes:
            put = {"TableName": self.table_name,
                   "Item": {"pk": {"S": pk}, "sk": {"S": sk}, "document": {"S": document}}}
            if previous is None:
                put.update(ConditionExpression="attribute_not_exists(#pk)", ExpressionAttributeNames={"#pk": "pk"})
            else:
                put.update(ConditionExpression="#document = :previous",
                           ExpressionAttributeNames={"#document": "document"},
                           ExpressionAttributeValues={":previous": {"S": previous}})
            items.append({"Put": put})
        try:
            response = self.client.transact_write_items(TransactItems=items, ClientRequestToken=uuid4().hex)
            if (not isinstance(response, dict) or "Error" in response
                    or ("ResponseMetadata" in response and (
                        not isinstance(response["ResponseMetadata"], dict)
                        or response["ResponseMetadata"].get("HTTPStatusCode") != 200))):
                raise ValueError
        except Exception as error:
            response = getattr(error, "response", None)
            if isinstance(response, dict) and isinstance(response.get("Error"), dict):
                reasons = response.get("CancellationReasons")
                if (response["Error"].get("Code") == "TransactionCanceledException"
                        and isinstance(reasons, list) and len(reasons) == len(items)
                        and all(isinstance(reason, dict) and reason.get("Code") in {"None", "ConditionalCheckFailed"}
                                for reason in reasons)
                        and any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons)):
                    raise _WriteConflict() from None
            # Never read back an ambiguous commit and turn it into permission.
            raise InvocationStoreUnavailable() from None
