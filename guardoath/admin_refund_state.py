"""Signed full-refund intents, permanent charge claims and bounded observations.

The original intent and every outcome are immutable. A transaction advances a
signed control pointer only with its new immutable outcome. An uncertain claim
never expires or authorizes another dispatch; even a failed refund keeps it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from guardoath.admin_cancellation_provider import canonical, digest

CONTRACT = "stripe-invoice-refund-v1"
HEX = r"^[a-f0-9]{64}$"
MAX_REVISION = (1 << 53) - 1
MAX_TIMESTAMP = 253402300799
MAX_OBSERVATIONS = 3
SNAPSHOT_FIELDS = (
    "account_id", "livemode", "customer_id", "subscription_id", "invoice_id",
    "charge_id", "payment_intent_id", "amount_minor", "currency", "expected_price_id",
)
STATUS_RANK = {"indeterminate": 0, "pending": 1, "verified": 2, "failed": 2}
PROVIDER_STATUS = {
    "pending": ("pending", "requires_action"),
    "verified": ("succeeded",),
    "failed": ("failed", "canceled"),
}


class RefundStateUnavailable(Exception):
    """Persistence or evidence is uncertain; no provider write is authorized."""


class RefundStateConflict(Exception):
    """The permanent charge claim or a newer signed observation already exists."""


class ProviderSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    account_id: str = Field(pattern=r"^acct_[A-Za-z0-9_]{1,96}$")
    livemode: bool
    customer_id: str = Field(pattern=r"^cus_[A-Za-z0-9_]{1,96}$")
    subscription_id: str = Field(pattern=r"^sub_[A-Za-z0-9_]{1,96}$")
    invoice_id: str = Field(pattern=r"^in_[A-Za-z0-9_]{1,96}$")
    charge_id: str = Field(pattern=r"^ch_[A-Za-z0-9_]{1,96}$")
    payment_intent_id: str = Field(pattern=r"^pi_[A-Za-z0-9_]{1,96}$")
    amount_minor: int = Field(ge=1, le=MAX_REVISION)
    currency: str = Field(pattern=r"^[a-z]{3}$")
    expected_price_id: str = Field(pattern=r"^price_[A-Za-z0-9_]{1,96}$")


class ProviderProof(ProviderSnapshot):
    action_id: str = Field(pattern=HEX)
    refund_id: str = Field(pattern=r"^re_[A-Za-z0-9_]{1,96}$")
    status: Literal["pending", "requires_action", "succeeded", "failed", "canceled"]
    refund_created: int = Field(ge=1, le=MAX_TIMESTAMP)


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    contract: Literal["stripe-invoice-refund-v1"]
    action_id: str = Field(pattern=HEX)
    target_id: str = Field(pattern=HEX)
    generation: Literal[1]
    signature: str = Field(pattern=HEX)


class Intent(Envelope, ProviderSnapshot):
    kind: Literal["intent"]
    actor_sub: str = Field(pattern=r"^[A-Za-z0-9_-]{12,128}$")
    tenant_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    expected_revision: int = Field(ge=1, le=MAX_REVISION)
    request_hash: str = Field(pattern=HEX)
    provider_hash: str = Field(pattern=HEX)
    reason: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 .,;:_()/#-]{9,239}$")
    created_at: int = Field(ge=1, le=MAX_TIMESTAMP)


class Outcome(Envelope):
    kind: Literal["outcome"]
    intent_hash: str = Field(pattern=HEX)
    sequence: Literal[1]
    previous_hash: None
    status: Literal["indeterminate", "pending", "verified", "failed"]
    recorded_at: int = Field(ge=1, le=MAX_TIMESTAMP)
    proof: ProviderProof | None
    proof_hash: str | None = Field(pattern=HEX)


class Observation(Envelope):
    kind: Literal["observation"]
    intent_hash: str = Field(pattern=HEX)
    sequence: int = Field(ge=2, le=MAX_OBSERVATIONS)
    previous_hash: str = Field(pattern=HEX)
    status: Literal["pending", "verified", "failed"]
    recorded_at: int = Field(ge=1, le=MAX_TIMESTAMP)
    proof: ProviderProof
    proof_hash: str = Field(pattern=HEX)


class Control(Envelope):
    kind: Literal["control"]
    intent_hash: str = Field(pattern=HEX)
    state: Literal["RESERVED", "DISPATCHING", "INDETERMINATE", "PENDING", "VERIFIED", "FAILED"]
    dispatch_started: bool
    revision: int = Field(ge=1, le=2 + MAX_OBSERVATIONS)
    outcome_sequence: int = Field(ge=0, le=MAX_OBSERVATIONS)
    outcome_hash: str | None = Field(pattern=HEX)


MODELS = {"intent": Intent, "outcome": Outcome, "observation": Observation, "control": Control}


def intent_hash(value: dict) -> str:
    return digest({k: v for k, v in value.items() if k != "signature"})


def outcome_hash(value: dict) -> str:
    return intent_hash(value)


def target_id(snapshot: dict) -> str:
    return digest(["guardoath-stripe-invoice-refund-target-v1", snapshot["account_id"],
                   snapshot["livemode"], snapshot["charge_id"]])


def _proof_identity(proof: dict) -> dict:
    return {k: v for k, v in proof.items() if k != "status"}


class DynamoRefundState:
    ATTRIBUTE = "financial_operation"

    def __init__(self, client, table: str, key: bytes):
        if (not isinstance(table, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", table)
                or not isinstance(key, bytes) or len(key) < 32
                or not all(callable(getattr(client, name, None)) for name in
                           ("get_item", "put_item", "transact_write_items"))):
            raise ValueError("Independent persistent refund control required")
        self.client, self.table_name, self.audit_key = client, table, key
        self._key = hmac.new(key, CONTRACT.encode("ascii"), hashlib.sha256).digest()

    @staticmethod
    def row_id(kind: str, identifier: str) -> str:
        if kind not in MODELS or not isinstance(identifier, str) or not re.fullmatch(HEX, identifier):
            raise RefundStateUnavailable() from None
        return "financial-ir1:" + kind + ":" + identifier

    @staticmethod
    def observation_id(action_id: str, sequence: int) -> str:
        if (not isinstance(action_id, str) or not re.fullmatch(HEX, action_id)
                or type(sequence) is not int or not 2 <= sequence <= MAX_OBSERVATIONS):
            raise RefundStateUnavailable() from None
        return digest([CONTRACT, "observation", action_id, sequence])

    @classmethod
    def _identifier(cls, value: dict) -> str:
        if value["kind"] == "control":
            return value["target_id"]
        if value["kind"] == "observation":
            return cls.observation_id(value["action_id"], value["sequence"])
        return value["action_id"]

    def _seal(self, value: dict) -> dict:
        try:
            unsigned = {k: v for k, v in value.items() if k != "signature"}
            signed = {**unsigned, "signature": hmac.new(self._key,
                canonical(unsigned).encode("ascii"), hashlib.sha256).hexdigest()}
            self._validate(signed)
            return signed
        except Exception:
            raise RefundStateUnavailable() from None

    @staticmethod
    def _validate(value: dict) -> None:
        try:
            if type(value["version"]) is not int or type(value["generation"]) is not int:
                raise ValueError()
            MODELS[value["kind"]].model_validate(value)
            kind = value["kind"]
            if kind == "intent":
                snapshot = {k: value[k] for k in SNAPSHOT_FIELDS}
                if value["provider_hash"] != digest(snapshot) or value["target_id"] != target_id(snapshot):
                    raise ValueError()
            elif kind in ("outcome", "observation"):
                if type(value["sequence"]) is not int:
                    raise ValueError()
                proof = value["proof"]
                if value["status"] == "indeterminate":
                    if proof is not None or value["proof_hash"] is not None:
                        raise ValueError()
                elif (not isinstance(proof, dict) or value["proof_hash"] != digest(proof)
                        or proof["status"] not in PROVIDER_STATUS[value["status"]]
                        or proof["action_id"] != value["action_id"]
                        or proof["refund_created"] > value["recorded_at"]
                        or value["target_id"] != target_id(proof)):
                    raise ValueError()
            else:
                count, state = value["outcome_sequence"], value["state"]
                if count == 0:
                    if (value["outcome_hash"] is not None
                            or state != ("DISPATCHING" if value["dispatch_started"] else "RESERVED")):
                        raise ValueError()
                elif (value["outcome_hash"] is None
                        or state not in ("INDETERMINATE", "PENDING", "VERIFIED", "FAILED")
                        or (state == "INDETERMINATE" and count != 1)
                        or (state != "INDETERMINATE" and not value["dispatch_started"])):
                    raise ValueError()
                if value["revision"] != (2 if value["dispatch_started"] else 1) + count:
                    raise ValueError()
        except Exception:
            raise RefundStateUnavailable() from None

    def _verified_value(self, value: dict, kind: str) -> None:
        self._validate(value)
        if value["kind"] != kind or not hmac.compare_digest(value["signature"], self._seal(value)["signature"]):
            raise RefundStateUnavailable() from None

    @staticmethod
    def _require_writer() -> None:
        from guardoath.state_contracts import new_refund_state_enabled
        if not new_refund_state_enabled():
            raise RefundStateUnavailable() from None

    def verify_item(self, item: dict, kind: str, identifier: str) -> dict:
        try:
            if (set(item) != {"event_id", self.ATTRIBUTE}
                    or item["event_id"] != {"S": self.row_id(kind, identifier)}
                    or set(item[self.ATTRIBUTE]) != {"S"}):
                raise ValueError()
            raw = item[self.ATTRIBUTE]["S"]
            value = json.loads(raw) if isinstance(raw, str) and len(raw) <= 8192 else None
            self._verified_value(value, kind)
            if self._identifier(value) != identifier or canonical(value) != raw:
                raise ValueError()
            return value
        except Exception:
            raise RefundStateUnavailable() from None

    def read(self, kind: str, identifier: str) -> dict | None:
        try:
            result = self.client.get_item(TableName=self.table_name,
                Key={"event_id": {"S": self.row_id(kind, identifier)}}, ConsistentRead=True)
            if not isinstance(result, dict):
                raise ValueError()
            return self.verify_item(result["Item"], kind, identifier) if "Item" in result else None
        except Exception:
            raise RefundStateUnavailable() from None

    def _put_request(self, value: dict, previous: dict | None = None) -> dict:
        request = {"TableName": self.table_name,
            "Item": {"event_id": {"S": self.row_id(value["kind"], self._identifier(value))},
                     self.ATTRIBUTE: {"S": canonical(value)}},
            "ConditionExpression": "attribute_not_exists(event_id)"}
        if previous is not None:
            request.update(ConditionExpression="attribute_exists(event_id) AND #control = :original",
                ExpressionAttributeNames={"#control": self.ATTRIBUTE},
                ExpressionAttributeValues={":original": {"S": canonical(previous)}})
        return request

    def _transact(self, operations: list[dict], *, conflicts: bool = True) -> None:
        try:
            result = self.client.transact_write_items(TransactItems=operations)
            if not isinstance(result, dict):
                raise ValueError()
        except Exception as exc:
            response = getattr(exc, "response", None)
            error = response.get("Error") if isinstance(response, dict) else None
            if conflicts and isinstance(error, dict) and error.get("Code") in (
                    "ConditionalCheckFailedException", "TransactionCanceledException"):
                raise RefundStateConflict() from None
            raise RefundStateUnavailable() from None

    @staticmethod
    def _linked(value: dict, intent: dict) -> None:
        if (value["action_id"] != intent["action_id"] or value["target_id"] != intent["target_id"]
                or value["generation"] != intent["generation"] or value["intent_hash"] != intent_hash(intent)):
            raise RefundStateUnavailable() from None
        if value["kind"] in ("outcome", "observation"):
            proof = value["proof"]
            if value["recorded_at"] < intent["created_at"] or (proof is not None and
                    (any(proof[k] != intent[k] for k in SNAPSHOT_FIELDS)
                     or proof["refund_created"] < intent["created_at"])):
                raise RefundStateUnavailable() from None

    def _history(self, action_id: str) -> tuple[dict | None, dict | None, dict | None]:
        intent = self.read("intent", action_id)
        journal = [self.read("outcome", action_id), *[
            self.read("observation", self.observation_id(action_id, sequence))
            for sequence in range(2, MAX_OBSERVATIONS + 1)]]
        if intent is None:
            if any(value is not None for value in journal):
                raise RefundStateUnavailable() from None
            return None, None, None
        control = self.read("control", intent["target_id"])
        if control is None:
            raise RefundStateUnavailable() from None
        self._linked(control, intent)
        count = control["outcome_sequence"]
        # The journal is bounded, so also reject an authentic old control replay
        # that tries to conceal an already-recorded later observation.
        if any(value is not None for value in journal[count:]):
            raise RefundStateUnavailable() from None
        if count == 0:
            return intent, None, control
        previous = None
        for sequence in range(1, count + 1):
            value = journal[sequence - 1]
            if value is None:
                raise RefundStateUnavailable() from None
            self._linked(value, intent)
            if value["sequence"] != sequence:
                raise RefundStateUnavailable() from None
            if previous is not None and (value["previous_hash"] != outcome_hash(previous)
                    or value["recorded_at"] < previous["recorded_at"]
                    or STATUS_RANK[value["status"]] <= STATUS_RANK[previous["status"]]
                    or (previous["proof"] is not None
                        and _proof_identity(value["proof"]) != _proof_identity(previous["proof"]))):
                raise RefundStateUnavailable() from None
            previous = value
        if (control["outcome_hash"] != outcome_hash(previous)
                or control["state"] != previous["status"].upper()
                or (previous["status"] != "indeterminate" and not control["dispatch_started"])):
            raise RefundStateUnavailable() from None
        return intent, previous, control

    def history(self, action_id: str) -> tuple[dict | None, dict | None]:
        intent, latest, _ = self._history(action_id)
        return intent, latest

    def reserve(self, fields: dict, *, expected_control_hash: str) -> tuple[dict, dict]:
        self._require_writer()
        allowed = {*SNAPSHOT_FIELDS, "action_id", "target_id", "actor_sub", "tenant_id",
                   "expected_revision", "request_hash", "provider_hash", "reason", "created_at"}
        if not isinstance(fields, dict) or set(fields) != allowed:
            raise RefundStateUnavailable() from None
        intent = self._seal({"version": 1, "contract": CONTRACT, "kind": "intent",
                             **fields, "generation": 1})
        previous = self.read("control", intent["target_id"])
        # A full-refund target has no legitimate successor, including after a
        # failed or externally changed refund. Its claim is permanent.
        if previous is not None or expected_control_hash != digest(None):
            raise RefundStateConflict() from None
        control = self._seal({"version": 1, "contract": CONTRACT, "kind": "control",
            "action_id": intent["action_id"], "target_id": intent["target_id"], "generation": 1,
            "intent_hash": intent_hash(intent), "state": "RESERVED", "dispatch_started": False,
            "revision": 1, "outcome_sequence": 0, "outcome_hash": None})
        self._transact([{"Put": self._put_request(control)}, {"Put": self._put_request(intent)}])
        return control, intent

    def dispatching(self, control: dict, *, tenant_table: str, tenant_id: str,
                    expected_revision: int, original_document: str) -> dict:
        self._require_writer()
        self._verified_value(control, "control")
        if (not isinstance(tenant_table, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", tenant_table)
                or tenant_table == self.table_name or not isinstance(original_document, str)
                or not 0 < len(original_document.encode("utf-8")) <= 350000
                or type(expected_revision) is not int or not 1 <= expected_revision <= MAX_REVISION
                or control["state"] != "RESERVED" or control["revision"] != 1):
            raise RefundStateUnavailable() from None
        intent = self.read("intent", control["action_id"])
        if intent is None or intent["tenant_id"] != tenant_id or intent["expected_revision"] != expected_revision:
            raise RefundStateUnavailable() from None
        self._linked(control, intent)
        following = self._seal({**control, "state": "DISPATCHING", "dispatch_started": True, "revision": 2})
        self._transact([
            {"ConditionCheck": {"TableName": tenant_table, "Key": {"tenant_id": {"S": tenant_id}},
                "ConditionExpression": "attribute_exists(tenant_id) AND #revision = :prior AND #record = :original",
                "ExpressionAttributeNames": {"#revision": "revision", "#record": "record"},
                "ExpressionAttributeValues": {":prior": {"N": str(expected_revision)},
                                              ":original": {"S": original_document}}}},
            {"Put": self._put_request(following, control)}], conflicts=False)
        # Only this successful transaction response grants dispatch authority.
        # Reading DISPATCHING after a timeout cannot recreate that authority.
        return following

    def outcome(self, intent: dict, status: str, now: int, proof: dict | None = None) -> dict:
        return self._append(intent, status, now, proof, initial=True)

    def observe(self, intent: dict, status: str, now: int, proof: dict) -> dict:
        return self._append(intent, status, now, proof, initial=False)

    def _append(self, intent: dict, status: str, now: int, proof: dict | None, *, initial: bool) -> dict:
        self._require_writer()
        self._verified_value(intent, "intent")
        saved, previous, control = self._history(intent["action_id"])
        if (saved != intent or control is None or type(now) is not int
                or not intent["created_at"] <= now <= MAX_TIMESTAMP
                or not isinstance(status, str) or status not in STATUS_RANK
                or (status != "indeterminate" and not control["dispatch_started"])):
            raise RefundStateUnavailable() from None
        if proof is not None:
            try:
                ProviderProof.model_validate(proof)
                if (status == "indeterminate" or proof["status"] not in PROVIDER_STATUS[status]
                        or proof["action_id"] != intent["action_id"]
                        or any(proof[k] != intent[k] for k in SNAPSHOT_FIELDS)
                        or not intent["created_at"] <= proof["refund_created"] <= now):
                    raise ValueError()
            except Exception:
                raise RefundStateUnavailable() from None
        elif status != "indeterminate":
            raise RefundStateUnavailable() from None
        if previous is not None:
            if now < previous["recorded_at"]:
                raise RefundStateUnavailable() from None
            if status == previous["status"] and proof == previous["proof"]:
                return previous
            if (previous["status"] == status == "pending" and proof is not None
                    and _proof_identity(proof) == _proof_identity(previous["proof"])):
                # Preserve the truthful original provider status/time; repeated
                # pending reads do not consume journal capacity or poll writes.
                return previous
            if (initial or STATUS_RANK[status] <= STATUS_RANK[previous["status"]]
                    or (previous["proof"] is not None and
                        (proof is None or _proof_identity(proof) != _proof_identity(previous["proof"])))):
                raise RefundStateConflict() from None
        sequence = 1 if previous is None else previous["sequence"] + 1
        if sequence > MAX_OBSERVATIONS:
            raise RefundStateUnavailable() from None
        value = self._seal({"version": 1, "contract": CONTRACT,
            "kind": "outcome" if sequence == 1 else "observation",
            "action_id": intent["action_id"], "target_id": intent["target_id"], "generation": 1,
            "intent_hash": intent_hash(intent), "sequence": sequence,
            "previous_hash": None if previous is None else outcome_hash(previous),
            "status": status, "recorded_at": now, "proof": proof,
            "proof_hash": None if proof is None else digest(proof)})
        following = self._seal({**control, "state": status.upper(), "revision": control["revision"] + 1,
            "outcome_sequence": sequence, "outcome_hash": outcome_hash(value)})
        self._transact([{"Put": self._put_request(following, control)}, {"Put": self._put_request(value)}])
        return value
