"""Versioned financial intent/outcome receipts and separately mutable target control.

All records live in the existing independent audit table. Immutable envelopes
are insert-only. A full verified document CAS fences each dispatch; unknown
claims never expire. Stable storage identities do not depend on signing keys.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from guardoath.admin_cancellation_provider import canonical, digest

CONTRACT = "stripe-period-end-cancellation-v1"
HEX = r"^[a-f0-9]{64}$"
MAX_REVISION = (1 << 53) - 1


class CancellationStateUnavailable(Exception):
    """Unverifiable or uncertain persistence never authorizes a provider write."""


class CancellationStateConflict(Exception):
    """Another durable action or tenant change owns this target."""


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    contract: Literal["stripe-period-end-cancellation-v1"]
    action_id: str = Field(pattern=HEX)
    target_id: str = Field(pattern=HEX)
    generation: int = Field(ge=1, le=1_000_000_000)
    signature: str = Field(pattern=HEX)


class Intent(Envelope):
    kind: Literal["intent"]
    actor_sub: str = Field(pattern=r"^[A-Za-z0-9_-]{12,128}$")
    tenant_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    expected_revision: int = Field(ge=1, le=MAX_REVISION)
    request_hash: str = Field(pattern=HEX)
    provider_hash: str = Field(pattern=HEX)
    reason: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 .,;:_()/#-]{9,239}$")
    effective_at: int = Field(ge=1, le=253402300799)
    created_at: int = Field(ge=1, le=253402300799)
    before: Literal[False]
    after: Literal[True]


class Outcome(Envelope):
    kind: Literal["outcome"]
    intent_hash: str = Field(pattern=HEX)
    status: Literal["verified", "indeterminate"]
    recorded_at: int = Field(ge=1, le=253402300799)
    update_hash: str | None = Field(pattern=HEX)
    verification_hash: str | None = Field(pattern=HEX)


class Control(Envelope):
    kind: Literal["control"]
    intent_hash: str = Field(pattern=HEX)
    state: Literal["RESERVED", "DISPATCHING", "VERIFIED", "BLOCKED"]
    revision: int = Field(ge=1, le=3)


MODELS = {"intent": Intent, "outcome": Outcome, "control": Control}


def intent_hash(value: dict) -> str:
    return digest({k: v for k, v in value.items() if k != "signature"})


class DynamoCancellationState:
    ATTRIBUTE = "financial_operation"

    def __init__(self, client, table: str, key: bytes):
        if (not isinstance(table, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", table)
                or not isinstance(key, bytes) or len(key) < 32
                or not all(callable(getattr(client, name, None)) for name in
                           ("get_item", "put_item", "transact_write_items"))):
            raise ValueError("Independent persistent financial control required")
        self.client, self.table_name = client, table
        self._key = hmac.new(key, CONTRACT.encode("ascii"), hashlib.sha256).digest()

    @staticmethod
    def row_id(kind: str, identifier: str) -> str:
        if kind not in MODELS or not isinstance(identifier, str) or not re.fullmatch(HEX, identifier):
            raise CancellationStateUnavailable() from None
        return "financial-sc1:" + kind + ":" + identifier

    def _seal(self, value: dict) -> dict:
        unsigned = {k: v for k, v in value.items() if k != "signature"}
        signed = {**unsigned, "signature": hmac.new(self._key,
            canonical(unsigned).encode("ascii"), hashlib.sha256).hexdigest()}
        self._validate(signed)
        return signed

    def _validate(self, value: dict) -> None:
        try:
            if type(value["version"]) is not int:
                raise ValueError()
            MODELS[value["kind"]].model_validate(value)
            if value["kind"] == "intent" and (value["created_at"] >= value["effective_at"]
                    or value["before"] is not False or value["after"] is not True):
                raise ValueError()
            if value["kind"] == "outcome":
                hashes = (value["update_hash"], value["verification_hash"])
                if ((value["status"] == "verified" and any(v is None for v in hashes)) or
                        (value["status"] == "indeterminate" and any(v is not None for v in hashes))):
                    raise ValueError()
            if value["kind"] == "control" and value["revision"] not in {
                    "RESERVED": (1,), "DISPATCHING": (2,), "VERIFIED": (3,), "BLOCKED": (2,3)}[value["state"]]:
                raise ValueError()
        except Exception:
            raise CancellationStateUnavailable() from None

    def _verified_value(self, value: dict, kind: str) -> None:
        self._validate(value)
        if value["kind"] != kind or not hmac.compare_digest(value["signature"], self._seal(value)["signature"]):
            raise CancellationStateUnavailable() from None

    @staticmethod
    def _require_writer() -> None:
        from guardoath.state_contracts import new_cancellation_state_enabled
        if not new_cancellation_state_enabled():
            raise CancellationStateUnavailable() from None

    def verify_item(self, item: dict, kind: str, identifier: str) -> dict:
        try:
            key = self.row_id(kind, identifier)
            if (set(item) != {"event_id", self.ATTRIBUTE}
                    or item["event_id"] != {"S": key}
                    or set(item[self.ATTRIBUTE]) != {"S"}):
                raise ValueError()
            raw = item[self.ATTRIBUTE]["S"]
            value = json.loads(raw) if isinstance(raw, str) and len(raw) <= 8192 else None
            self._validate(value)
            if (value["kind"] != kind or value["target_id" if kind == "control" else "action_id"] != identifier
                    or canonical(value) != raw
                    or not hmac.compare_digest(value["signature"], self._seal(value)["signature"])):
                raise ValueError()
            return value
        except Exception:
            raise CancellationStateUnavailable() from None

    def read(self, kind: str, identifier: str) -> dict | None:
        try:
            result = self.client.get_item(TableName=self.table_name,
                Key={"event_id": {"S": self.row_id(kind, identifier)}}, ConsistentRead=True)
            if not isinstance(result, dict):
                raise ValueError()
            return self.verify_item(result["Item"], kind, identifier) if "Item" in result else None
        except Exception:
            raise CancellationStateUnavailable() from None

    def _put_request(self, value: dict, previous: dict | None = None) -> dict:
        identifier = value["target_id" if value["kind"] == "control" else "action_id"]
        request = {"TableName": self.table_name,
            "Item": {"event_id": {"S": self.row_id(value["kind"], identifier)},
                     self.ATTRIBUTE: {"S": canonical(value)}},
            "ConditionExpression": "attribute_not_exists(event_id)"}
        if previous is not None:
            request.update(ConditionExpression="attribute_exists(event_id) AND #control = :original",
                ExpressionAttributeNames={"#control": self.ATTRIBUTE},
                ExpressionAttributeValues={":original": {"S": canonical(previous)}})
        return request

    def _put(self, value: dict, previous: dict | None = None) -> dict:
        try:
            result = self.client.put_item(**self._put_request(value, previous))
            if not isinstance(result, dict):
                raise ValueError()
            return value
        except Exception as exc:
            response = getattr(exc, "response", None)
            error = response.get("Error") if isinstance(response, dict) else None
            if isinstance(error, dict) and error.get("Code") == "ConditionalCheckFailedException":
                raise CancellationStateConflict() from None
            raise CancellationStateUnavailable() from None

    def history(self, action_id: str) -> tuple[dict | None, dict | None]:
        intent = self.read("intent", action_id)
        outcome = self.read("outcome", action_id)
        if outcome is not None and (intent is None or outcome["intent_hash"] != intent_hash(intent)
                or outcome["target_id"] != intent["target_id"]
                or outcome["generation"] != intent["generation"]
                or outcome["recorded_at"] < intent["created_at"]
                or (outcome["status"] == "verified" and
                    (outcome["update_hash"] != intent["provider_hash"]
                     or outcome["verification_hash"] != intent["provider_hash"]))):
            raise CancellationStateUnavailable() from None
        return intent, outcome

    def reserve(self, fields: dict, *, expected_control_hash: str) -> tuple[dict, dict]:
        self._require_writer()
        previous = self.read("control", fields["target_id"])
        # A preview made before an earlier action completed cannot masquerade
        # as a newly reviewed external reversal after that action completes.
        if not isinstance(expected_control_hash, str) or digest(previous) != expected_control_hash:
            raise CancellationStateConflict() from None
        generation = 1
        if previous is not None:
            old_intent, old_outcome = self.history(previous["action_id"])
            # Only verified immutable evidence permits a fresh generation after
            # fresh provider admission proves the prior schedule was reversed.
            # In-flight/unknown claims have no expiry, lease or steal operation.
            if (old_intent is None or old_outcome is None or old_outcome["status"] != "verified"
                    or previous["intent_hash"] != intent_hash(old_intent)
                    or previous["target_id"] != old_intent["target_id"]
                    or previous["generation"] != old_intent["generation"]
                    or previous["action_id"] == fields["action_id"]):
                raise CancellationStateConflict() from None
            generation = previous["generation"] + 1
        intent = self._seal({"version": 1, "contract": CONTRACT, "kind": "intent",
                            **fields, "generation": generation})
        control = self._seal({"version": 1, "contract": CONTRACT, "kind": "control",
            "action_id": intent["action_id"], "target_id": intent["target_id"],
            "generation": generation, "intent_hash": intent_hash(intent),
            "state": "RESERVED", "revision": 1})
        try:
            # Claim the target and action reference atomically. A losing action
            # key collision must not strand an unrelated subscription target.
            result = self.client.transact_write_items(TransactItems=[
                {"Put": self._put_request(control, previous)},
                {"Put": self._put_request(intent)}])
            if not isinstance(result, dict):
                raise ValueError()
            return control, intent
        except Exception as exc:
            response = getattr(exc, "response", None)
            error = response.get("Error") if isinstance(response, dict) else None
            if isinstance(error, dict) and error.get("Code") in (
                    "ConditionalCheckFailedException", "TransactionCanceledException"):
                raise CancellationStateConflict() from None
            raise CancellationStateUnavailable() from None

    def dispatching(self, control: dict, *, tenant_table: str, tenant_id: str,
                    expected_revision: int, original_document: str) -> dict:
        self._require_writer()
        self._verified_value(control, "control")
        intent = self.read("intent", control["action_id"])
        if (control["state"] != "RESERVED" or control["revision"] != 1
                or intent is None or control["intent_hash"] != intent_hash(intent)
                or intent["tenant_id"] != tenant_id or intent["expected_revision"] != expected_revision
                or tenant_table == self.table_name):
            raise CancellationStateUnavailable() from None
        next_control = self._seal({**control, "state": "DISPATCHING", "revision": 2})
        try:
            # This transaction grants one worker dispatch authority only while
            # the exact reviewed tenant document/revision still exists. It does
            # not write the tenant or its separately counted usage map.
            result = self.client.transact_write_items(TransactItems=[
                {"ConditionCheck": {"TableName": tenant_table,
                    "Key": {"tenant_id": {"S": tenant_id}},
                    "ConditionExpression": "attribute_exists(tenant_id) AND #revision = :prior AND #record = :original",
                    "ExpressionAttributeNames": {"#revision": "revision", "#record": "record"},
                    "ExpressionAttributeValues": {":prior": {"N": str(expected_revision)},
                                                  ":original": {"S": original_document}}}},
                {"Put": self._put_request(next_control, control)}])
            if not isinstance(result, dict):
                raise ValueError()
            return next_control
        except Exception:
            # Even a lost successful transaction response grants this caller
            # no dispatch permission. Reading DISPATCHING cannot recreate it.
            raise CancellationStateUnavailable() from None

    def outcome(self, intent: dict, status: str, now: int) -> dict:
        self._require_writer()
        self._verified_value(intent, "intent")
        saved = self.read("intent", intent["action_id"])
        control = self.read("control", intent["target_id"])
        if (saved != intent or control is None or control["intent_hash"] != intent_hash(intent)
                or type(now) is not int or now < intent["created_at"]
                or control["state"] not in ("RESERVED", "DISPATCHING")
                or (status == "verified" and control["state"] != "DISPATCHING")):
            raise CancellationStateUnavailable() from None
        value = self._seal({"version": 1, "contract": CONTRACT, "kind": "outcome",
            "action_id": intent["action_id"], "target_id": intent["target_id"],
            "generation": intent["generation"], "intent_hash": intent_hash(intent),
            "status": status, "recorded_at": now,
            "update_hash": intent["provider_hash"] if status == "verified" else None,
            "verification_hash": intent["provider_hash"] if status == "verified" else None})
        return self._put(value)

    def finish(self, control: dict, *, verified: bool) -> None:
        self._require_writer()
        self._verified_value(control, "control")
        intent, outcome = self.history(control["action_id"])
        if (type(verified) is not bool or control["state"] not in ("RESERVED", "DISPATCHING")
                or intent is None or control["intent_hash"] != intent_hash(intent)
                or (verified and (control["state"] != "DISPATCHING" or outcome is None
                                  or outcome["status"] != "verified"))):
            raise CancellationStateUnavailable() from None
        value = self._seal({**control, "state": "VERIFIED" if verified else "BLOCKED",
                            "revision": control["revision"] + 1})
        self._put(value, control)
