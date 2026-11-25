"""Signed, durable source-preparation requests in the existing admin audit table.

One fixed catalog control serializes every owner and signing key. Acknowledging
its conditional REQUESTED -> PREPARING write grants one worker attempt. Reading
that state later never grants a replacement attempt. Prepared source is not a
remote publication, accepted CI or a deployment; this version never releases
the global control, expires it or permits another generation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re

from guardoath.catalog_metadata import _safe_text, strict_catalog_json

CONTRACT = "catalog-publication-v1"
CATALOG_ID = hashlib.sha256(b"guardoath:source-shipped-catalog:v1").hexdigest()
EMPTY_CONTROL_HASH = "0" * 64
MAX_DOCUMENT_BYTES = 16384
_HEX = re.compile(r"^[a-f0-9]{64}$")
_SHA = re.compile(r"^[a-f0-9]{40}$")
_OWNER = re.compile(r"^[0-9a-fA-F-]{20,80}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
_VERSION = re.compile(r"^[1-9][0-9]{0,8}$")
_ID = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
_RULE = re.compile(r"^[A-Za-z0-9_.:-]{3,100}$")
_FORMAT = "guardoath.reviewed-catalog-metadata-source-plan.v1"
_PLAN_FIELDS = frozenset({
    "format", "status", "review_id", "draft_id", "registry_version",
    "candidate_registry_version", "reviewed_source_sha256",
    "reviewed_proposal_sha256", "candidate_metadata_sha256",
    "rollback_source_sha256", "target", "changed_fields", "changed_metadata",
    "forward", "rollback", "round_trip_verified", "hashes_are_not_signatures",
    "executable_source_committed", "published", "tenant_policies_changed",
    "detector_behavior_changed", "acceptance_tests_run",
})
_INTENT_FIELDS = frozenset({
    "request_id", "actor_sub", "review_id", "draft_id", "review_sha256",
    "draft_sha256", "base_source_sha", "operation", "registry_version",
    "candidate_registry_version", "baseline_sha256", "candidate_metadata_sha256",
    "target", "plan", "plan_sha256", "reason", "created_at", "request_hash",
})
_PREPARED_FIELDS = frozenset({
    "base_source_sha", "candidate_source_sha", "history_sha256",
    "candidate_metadata_sha256", "candidate_registry_version", "recorded_at",
})
_ENVELOPE = frozenset({"version", "contract", "kind", "signature"})


class PublicationConflict(Exception):
    """The request, reviewed control or single worker claim conflicts."""


class PublicationUnavailable(Exception):
    """Unverifiable or uncertain evidence cannot authorize source execution."""


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def document_hash(value: dict) -> str:
    """Hash canonical evidence, excluding only its top-level signature."""
    try:
        if type(value) is not dict:
            raise ValueError()
        unsigned = {key: item for key, item in value.items() if key != "signature"}
        return hashlib.sha256(_canonical(unsigned).encode("ascii")).hexdigest()
    except Exception:
        raise PublicationUnavailable() from None


def _matches(pattern, value) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _timestamp(value) -> bool:
    return type(value) is int and 1 <= value <= 253402300799


def _target(value) -> bool:
    return (type(value) is dict and set(value) == {"category_id", "check_id", "rule_id"}
            and _matches(_ID, value["category_id"])
            and _matches(_ID, value["check_id"])
            and _matches(_RULE, value["rule_id"]))


def _plan(value: dict, intent: dict) -> None:
    """Validate all export structure/bindings without consulting mutable source.

    Source and candidate fingerprints still require the trusted worker's actual
    checkout verification. A self-consistent plan alone grants no publication.
    """
    if (type(value) is not dict or set(value) != _PLAN_FIELDS
            or value["format"] != _FORMAT or value["status"] != "review_only_not_published"
            or value["round_trip_verified"] is not True
            or value["hashes_are_not_signatures"] is not True
            or any(value[key] is not False for key in (
                "executable_source_committed", "published", "tenant_policies_changed",
                "detector_behavior_changed", "acceptance_tests_run"))
            or any(not _matches(_HEX, value[key]) for key in (
                "review_id", "draft_id", "reviewed_source_sha256", "reviewed_proposal_sha256",
                "candidate_metadata_sha256", "rollback_source_sha256"))):
        raise ValueError()
    for key in ("review_id", "draft_id", "registry_version", "candidate_registry_version",
                "candidate_metadata_sha256", "target"):
        if value[key] != intent[key]:
            raise ValueError()
    if (not _target(value["target"])
            or value["reviewed_source_sha256"] != intent["baseline_sha256"]
            or value["rollback_source_sha256"] != intent["baseline_sha256"]
            or document_hash(value) != intent["plan_sha256"]):
        raise ValueError()
    fields, changed = value["changed_fields"], value["changed_metadata"]
    if (type(fields) is not list or not 1 <= len(fields) <= 2
            or any(type(field) is not str or field not in {"title", "use_case"} for field in fields)
            or fields != sorted(set(fields)) or type(changed) is not dict or set(changed) != set(fields)):
        raise ValueError()
    step_fields = {"expected_registry_version", "candidate_registry_version",
                   "expected_metadata_sha256", "candidate_metadata_sha256", "changes"}
    forward, rollback = value["forward"], value["rollback"]
    if (type(forward) is not dict or type(rollback) is not dict
            or set(forward) != step_fields or set(rollback) != step_fields
            or forward["expected_registry_version"] != intent["registry_version"]
            or forward["candidate_registry_version"] != intent["candidate_registry_version"]
            or rollback["expected_registry_version"] != intent["candidate_registry_version"]
            or rollback["candidate_registry_version"] != intent["registry_version"]
            or forward["expected_metadata_sha256"] != intent["baseline_sha256"]
            or rollback["candidate_metadata_sha256"] != intent["baseline_sha256"]
            or forward["candidate_metadata_sha256"] != intent["candidate_metadata_sha256"]
            or rollback["expected_metadata_sha256"] != intent["candidate_metadata_sha256"]
            or type(forward["changes"]) is not dict or type(rollback["changes"]) is not dict
            or set(forward["changes"]) != set(fields) or set(rollback["changes"]) != set(fields)):
        raise ValueError()
    for field in fields:
        change, inverse = changed[field], rollback["changes"][field]
        if (type(change) is not dict or set(change) != {"before", "after"}
                or type(inverse) is not dict or set(inverse) != {"before", "after"}
                or change != forward["changes"][field]
                or inverse != {"before": change["after"], "after": change["before"]}
                or not _safe_text(change["before"], 120 if field == "title" else 500)
                or not _safe_text(change["after"], 120 if field == "title" else 500)
                or change["before"] == change["after"]
                or (field == "use_case" and not change["after"].startswith("Turn this on"))):
            raise ValueError()
    proposal = {"source_catalog_sha256": intent["baseline_sha256"],
                "expected_registry_version": intent["registry_version"],
                "candidate_registry_version": intent["candidate_registry_version"],
                **intent["target"], "changes": changed}
    fingerprint = hashlib.sha256(("guardoath-admin-catalog-proposal-v1\x00" +
                                  _canonical(proposal)).encode("utf-8")).hexdigest()
    if fingerprint != value["reviewed_proposal_sha256"]:
        raise ValueError()


class DynamoCatalogPublicationState:
    ATTRIBUTE = "catalog_publication"

    def __init__(self, client, table: str, key: bytes):
        if (type(table) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", table)
                or type(key) is not bytes or len(key) < 32
                or not all(callable(getattr(client, name, None)) for name in
                           ("get_item", "put_item", "transact_write_items"))):
            raise ValueError("Independent persistent catalog control required")
        self.client, self.table_name = client, table
        self._key = hmac.new(key, CONTRACT.encode("ascii"), hashlib.sha256).digest()

    @staticmethod
    def request_id(actor: str, action_reference: str) -> str:
        if not _matches(_OWNER, actor) or not _matches(_REFERENCE, action_reference):
            raise PublicationUnavailable() from None
        body = {"actor_sub": actor, "action_reference": action_reference}
        return hashlib.sha256(b"guardoath-catalog-publication-request-v1\x00" +
                              _canonical(body).encode("ascii")).hexdigest()

    @staticmethod
    def row_id(kind: str, identifier: str) -> str:
        if type(kind) is not str or kind not in {"intent", "control", "prepared"} or not _matches(_HEX, identifier):
            raise PublicationUnavailable() from None
        if kind == "control" and identifier != CATALOG_ID:
            raise PublicationUnavailable() from None
        return CONTRACT + ":" + kind + ":" + identifier

    @staticmethod
    def _require_writer() -> None:
        try:
            from guardoath.state_contracts import new_catalog_publication_state_enabled
            if new_catalog_publication_state_enabled() is not True:
                raise ValueError()
        except Exception:
            raise PublicationUnavailable() from None

    def _validate(self, value: dict) -> None:
        try:
            if (type(value) is not dict or type(value.get("version")) is not int
                    or value["version"] != 1 or value["contract"] != CONTRACT
                    or not _matches(_HEX, value["signature"])
                    or not _matches(_HEX, value["request_id"])):
                raise ValueError()
            kind = value["kind"]
            if kind == "intent":
                if (set(value) != _ENVELOPE | _INTENT_FIELDS | {"catalog_id"}
                        or value["catalog_id"] != CATALOG_ID
                        or not _matches(_OWNER, value["actor_sub"])
                        or not _matches(_SHA, value["base_source_sha"])
                        or value["operation"] not in ("publish_metadata", "reverse_latest")
                        or not _timestamp(value["created_at"])
                        or not _safe_text(value["reason"], 240) or len(value["reason"]) < 10
                        or not _target(value["target"])
                        or any(not _matches(_HEX, value[key]) for key in (
                            "review_id", "draft_id", "review_sha256", "draft_sha256", "baseline_sha256",
                            "candidate_metadata_sha256", "plan_sha256", "request_hash"))
                        or any(not _matches(_VERSION, value[key]) for key in (
                            "registry_version", "candidate_registry_version"))
                        or value["baseline_sha256"] == value["candidate_metadata_sha256"]
                        or int(value["candidate_registry_version"]) != int(value["registry_version"]) + 1):
                    raise ValueError()
                _plan(value["plan"], value)
            elif kind == "control":
                if (set(value) != _ENVELOPE | {"request_id", "catalog_id", "intent_hash", "state", "revision"}
                        or value["catalog_id"] != CATALOG_ID or not _matches(_HEX, value["intent_hash"])
                        or type(value["revision"]) is not int
                        or value["revision"] != {"REQUESTED": 1, "PREPARING": 2, "SOURCE_PREPARED": 3}.get(value["state"])):
                    raise ValueError()
            elif kind == "prepared":
                if (set(value) != _ENVELOPE | _PREPARED_FIELDS | {
                        "request_id", "intent_hash", "status", "remote_published", "ci_verified", "deployed"}
                        or value["status"] != "source_prepared"
                        or any(value[key] is not False for key in ("remote_published", "ci_verified", "deployed"))
                        or any(not _matches(_SHA, value[key]) for key in ("base_source_sha", "candidate_source_sha"))
                        or value["base_source_sha"] == value["candidate_source_sha"]
                        or any(not _matches(_HEX, value[key]) for key in (
                            "intent_hash", "history_sha256", "candidate_metadata_sha256"))
                        or not _matches(_VERSION, value["candidate_registry_version"])
                        or not _timestamp(value["recorded_at"])):
                    raise ValueError()
            else:
                raise ValueError()
            if len(_canonical(value).encode("ascii")) > MAX_DOCUMENT_BYTES:
                raise ValueError()
        except Exception:
            raise PublicationUnavailable() from None

    def _seal(self, value: dict) -> dict:
        try:
            if type(value) is not dict:
                raise ValueError()
            # Copy the complete nested plan: callers cannot mutate a returned
            # object into a different signed write through a shared reference.
            unsigned = strict_catalog_json(_canonical({k: v for k, v in value.items() if k != "signature"}))
            signature = hmac.new(self._key, _canonical(unsigned).encode("ascii"), hashlib.sha256).hexdigest()
            signed = {**unsigned, "signature": signature}
            self._validate(signed)
            return signed
        except Exception:
            raise PublicationUnavailable() from None

    def _verified(self, value: dict, kind: str) -> None:
        self._validate(value)
        if value["kind"] != kind or not hmac.compare_digest(value["signature"], self._seal(value)["signature"]):
            raise PublicationUnavailable() from None

    def verify_item(self, item: dict, kind: str, identifier: str) -> dict:
        try:
            key = self.row_id(kind, identifier)
            if (type(item) is not dict or set(item) != {"event_id", self.ATTRIBUTE}
                    or item["event_id"] != {"S": key}
                    or type(item[self.ATTRIBUTE]) is not dict or set(item[self.ATTRIBUTE]) != {"S"}):
                raise ValueError()
            raw = item[self.ATTRIBUTE]["S"]
            if type(raw) is not str or len(raw) > MAX_DOCUMENT_BYTES or len(raw.encode("utf-8")) > MAX_DOCUMENT_BYTES:
                raise ValueError()
            value = strict_catalog_json(raw)
            self._verified(value, kind)
            if (value["catalog_id" if kind == "control" else "request_id"] != identifier
                    or _canonical(value) != raw):
                raise ValueError()
            return value
        except Exception:
            raise PublicationUnavailable() from None

    def _read(self, kind: str, identifier: str) -> dict | None:
        try:
            response = self.client.get_item(TableName=self.table_name,
                Key={"event_id": {"S": self.row_id(kind, identifier)}}, ConsistentRead=True)
            self._acknowledged(response)
            return self.verify_item(response["Item"], kind, identifier) if "Item" in response else None
        except Exception:
            raise PublicationUnavailable() from None

    @staticmethod
    def _links(control: dict | None, intent: dict | None, prepared: dict | None) -> None:
        if control is None:
            if intent is not None or prepared is not None:
                raise PublicationUnavailable() from None
            return
        if (intent is None or control["request_id"] != intent["request_id"]
                or control["intent_hash"] != document_hash(intent)
                or (control["state"] == "SOURCE_PREPARED") != (prepared is not None)):
            raise PublicationUnavailable() from None
        if prepared is not None and (
                prepared["request_id"] != intent["request_id"]
                or prepared["intent_hash"] != document_hash(intent)
                or prepared["base_source_sha"] != intent["base_source_sha"]
                or prepared["candidate_metadata_sha256"] != intent["candidate_metadata_sha256"]
                or prepared["candidate_registry_version"] != intent["candidate_registry_version"]
                or prepared["recorded_at"] < intent["created_at"]):
            raise PublicationUnavailable() from None

    def _snapshot(self, request_id: str) -> tuple[dict | None, dict | None, dict | None]:
        control = self._read("control", CATALOG_ID)
        intent, prepared = self._read("intent", request_id), self._read("prepared", request_id)
        if control is not None and control["request_id"] != request_id:
            # Validate an occupied global control even while looking up a
            # different action reference. Any unrelated saved request is orphaned
            # because this contract never releases or reassigns the control.
            self._links(control, self._read("intent", control["request_id"]),
                        self._read("prepared", control["request_id"]))
            if intent is not None or prepared is not None:
                raise PublicationUnavailable() from None
        else:
            self._links(control, intent, prepared)
        return control, intent, prepared

    def read_control(self) -> dict | None:
        control = self._read("control", CATALOG_ID)
        if control is not None:
            self._links(control, self._read("intent", control["request_id"]),
                        self._read("prepared", control["request_id"]))
        return control

    def read_request(self, request_id: str) -> dict | None:
        return self.history(request_id)[0]

    def history(self, request_id: str) -> tuple[dict | None, dict | None]:
        _, intent, prepared = self._snapshot(request_id)
        return intent, prepared

    def _put_request(self, value: dict, previous: dict | None = None) -> dict:
        identifier = CATALOG_ID if value["kind"] == "control" else value["request_id"]
        request = {"TableName": self.table_name,
                   "Item": {"event_id": {"S": self.row_id(value["kind"], identifier)},
                            self.ATTRIBUTE: {"S": _canonical(value)}},
                   "ConditionExpression": "attribute_not_exists(event_id)"}
        if previous is not None:
            request.update(ConditionExpression="attribute_exists(event_id) AND #control = :original",
                ExpressionAttributeNames={"#control": self.ATTRIBUTE},
                ExpressionAttributeValues={":original": {"S": _canonical(previous)}})
        return request

    @staticmethod
    def _acknowledged(response) -> None:
        if (type(response) is not dict or set(response) - {
                "Item", "Attributes", "ConsumedCapacity", "ItemCollectionMetrics", "ResponseMetadata"}):
            raise PublicationUnavailable() from None
        if "ResponseMetadata" in response:
            metadata = response["ResponseMetadata"]
            if (type(metadata) is not dict or type(metadata.get("HTTPStatusCode")) is not int
                    or metadata["HTTPStatusCode"] != 200):
                raise PublicationUnavailable() from None

    @staticmethod
    def _same_request(first: dict, second: dict) -> bool:
        ignored = {"signature", "created_at"}
        return {k: v for k, v in first.items() if k not in ignored} == {
            k: v for k, v in second.items() if k not in ignored}

    def reserve(self, fields: dict, expected_control_hash: str) -> dict:
        self._require_writer()
        if type(fields) is not dict or set(fields) != _INTENT_FIELDS:
            raise PublicationUnavailable() from None
        if not _matches(_HEX, expected_control_hash):
            raise PublicationConflict() from None
        intent = self._seal({**fields, "version": 1, "contract": CONTRACT,
                             "kind": "intent", "catalog_id": CATALOG_ID})
        control, saved, _ = self._snapshot(intent["request_id"])
        if saved is not None:
            if not self._same_request(saved, intent):
                raise PublicationConflict() from None
            return saved
        if control is not None or expected_control_hash != EMPTY_CONTROL_HASH:
            raise PublicationConflict() from None
        requested = self._seal({"version": 1, "contract": CONTRACT, "kind": "control",
            "request_id": intent["request_id"], "catalog_id": CATALOG_ID,
            "intent_hash": document_hash(intent), "state": "REQUESTED", "revision": 1})
        try:
            response = self.client.transact_write_items(TransactItems=[
                {"Put": self._put_request(requested)}, {"Put": self._put_request(intent)}])
            self._acknowledged(response)
        except Exception:
            # A transaction may have committed despite a lost acknowledgement.
            # Recover only signed history; this path never grants worker authority.
            after, recovered, _ = self._snapshot(intent["request_id"])
            if recovered is not None and self._same_request(recovered, intent):
                return recovered
            if after is not None:
                raise PublicationConflict() from None
            raise PublicationUnavailable() from None
        _, recovered, _ = self._snapshot(intent["request_id"])
        if recovered is None or recovered != intent:
            raise PublicationUnavailable() from None
        return recovered

    def claim(self, request_id: str) -> dict:
        self._require_writer()
        control, intent, prepared = self._snapshot(request_id)
        if (intent is None or control is None or control["request_id"] != request_id
                or control["state"] != "REQUESTED" or prepared is not None):
            raise PublicationConflict() from None
        claimed = self._seal({**control, "state": "PREPARING", "revision": 2})
        try:
            self._acknowledged(self.client.put_item(**self._put_request(claimed, control)))
        except Exception:
            # No readback or retry can recreate the successful CAS response.
            # An uncertain claim remains occupied with no execution authority.
            raise PublicationUnavailable() from None
        return claimed

    def complete_preparation(self, request_id: str, claim: dict, fields: dict) -> dict:
        self._require_writer()
        self._verified(claim, "control")
        if (claim["request_id"] != request_id or claim["state"] != "PREPARING"
                or type(fields) is not dict or set(fields) != _PREPARED_FIELDS):
            raise PublicationUnavailable() from None
        control, intent, previous = self._snapshot(request_id)
        if intent is None or control is None or control["request_id"] != request_id:
            raise PublicationUnavailable() from None
        prepared = self._seal({**fields, "version": 1, "contract": CONTRACT, "kind": "prepared",
            "request_id": request_id, "intent_hash": document_hash(intent), "status": "source_prepared",
            "remote_published": False, "ci_verified": False, "deployed": False})
        finished = self._seal({**claim, "state": "SOURCE_PREPARED", "revision": 3})
        self._links(finished, intent, prepared)
        if previous is not None:
            if previous != prepared or control != finished:
                raise PublicationConflict() from None
            return previous
        if control != claim:
            raise PublicationConflict() from None
        try:
            response = self.client.transact_write_items(TransactItems=[
                {"Put": self._put_request(finished, claim)}, {"Put": self._put_request(prepared)}])
            self._acknowledged(response)
        except Exception:
            # The caller can inspect immutable history, but an ambiguous finish
            # must not be converted into permission to prepare source again.
            raise PublicationUnavailable() from None
        _, saved_intent, saved = self._snapshot(request_id)
        if saved_intent != intent or saved != prepared:
            raise PublicationUnavailable() from None
        return saved
