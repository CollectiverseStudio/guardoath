"""Signed post-preparation facts and one-attempt external-operation claims.

The v1 catalog control remains occupied throughout publication and deployment.
Only a verified terminal chain makes it eligible for a later exact CAS. Phase
receipts are immutable; no TTL, deletion, reset or readback grants a new attempt.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

from guardoath.admin_catalog_publication_state import (
    CATALOG_ID, DynamoCatalogPublicationState, PublicationConflict,
    PublicationUnavailable, _canonical, _matches, _timestamp, document_hash,
)
from guardoath.catalog_metadata import strict_catalog_json

CONTRACT = "catalog-publication-lifecycle-v1"
REPOSITORY = "CollectiverseStudio/guardoath"
MAX_DOCUMENT_BYTES = 65536
DEPLOYMENT_FORMAT = "guardoath.catalog-deployment-receipt.v1"
_HEX = re.compile(r"^[a-f0-9]{64}$")
_SHA = re.compile(r"^[a-f0-9]{40}$")
_VERSION = re.compile(r"^[1-9][0-9]{0,8}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{6,160}$")
_LAST_MODIFIED = re.compile(r"^[0-9]{4}-[0-9T:.+Z-]{14,60}$")
_ENVELOPE = {"version", "contract", "kind", "signature"}
_BINDINGS = {
    "request_id", "catalog_id", "intent_hash", "prepared_hash", "base_source_sha",
    "candidate_source_sha", "history_sha256", "candidate_metadata_sha256",
    "candidate_registry_version", "repository", "global_control_hash",
}
_RECEIPT = {"recorded_at", "invocation_id", "previous_control_hash",
            "receipt_hashes", "candidate_tree_sha", "status"}
_KINDS = ("remote", "ci", "deployment", "completion")
_STATES = {
    "PUBLISHING": (1, ()),
    "REMOTE_PUBLISHED": (2, ("remote",)),
    "CI_VERIFIED": (3, ("remote", "ci")),
    "DEPLOYING": (4, ("remote", "ci")),
    "DEPLOYED": (5, ("remote", "ci", "deployment")),
    "VERIFIED": (6, _KINDS),
}
_STATUS = {"remote": "remote_published", "ci": "ci_verified",
           "deployment": "deployed", "completion": "completed"}
_FIELDS = {
    "remote": {"candidate_tree_sha", "remote_head_sha", "recorded_at"},
    "ci": {"runs", "evidence_sha256", "recorded_at"},
    "deployment": {"receipt"},
    "completion": {"remote_head_sha", "verified_source_sha", "verified_code_sha256",
                   "verified_revision_id", "configuration_sha256", "last_modified", "recorded_at"},
}
_EXTRA = {
    "remote": {"remote_head_sha"},
    "ci": {"runs", "evidence_sha256"},
    "deployment": {"receipt"},
    "completion": _FIELDS["completion"] - {"recorded_at"} | {"production_live_acceptance"},
}
REQUIRED_WORKFLOWS = {
    "Tests": tuple(sorted(
        [f"test ({v})" for v in ("3.11", "3.12", "3.13", "3.14", "3.15")]
        + [f"local-pii ({v})" for v in ("3.12", "3.13", "3.14")]
        + ["windows-launcher (3.12)", "windows-launcher (3.13)",
           "dashboard", "optional-aws-adapter", "release-wheel"])),
    "Push on main": ("Analyze (actions)", "Analyze (javascript-typescript)", "Analyze (python)"),
    "Field Comparisons Evidence": ("customer-browser-acceptance", "strict-portfolio-and-scale"),
    "PII Reliability Gate": (
        "real-pii-tail (ubuntu-latest, py3.12)", "real-pii-tail (ubuntu-latest, py3.13)",
        "real-pii-tail (windows-latest, py3.12)"),
    "Stripe Reconciliation Acceptance": ("authenticated-billing-browser", "owner-billing-api-and-stores"),
    "Trusted Reference Acceptance": ("signed-reference",),
    "Deploy Script Acceptance": ("powershell-parse-and-guardrails",),
}
_DEPLOYMENT_FIELDS = {
    "format", "invocation_id", "source_sha", "code_sha256", "backup_code_sha256",
    "backup_source_sha", "original_revision_id", "upload_revision_id", "verified_revision_id",
    "last_modified", "configuration_sha256", "account_id", "region", "function_name",
    "stack_name", "distribution_id", "invalidation_id", "observed_at",
    "automatic_rollback_owned", "last_update_status", "release_source_parity_verified",
    "dashboard_ui_matches_local_source", "expected_source_sha", "live_source_sha",
    "verified_asset_count", "expected_asset_count", "production_live_acceptance",
}


def _positive(value) -> bool:
    return type(value) is int and 1 <= value <= 2**63 - 1


def _digest(value) -> bool:
    try:
        return (type(value) is str and len(value) == 44
                and len(base64.b64decode(value, validate=True)) == 32
                and base64.b64encode(base64.b64decode(value)).decode("ascii") == value)
    except Exception:
        return False


def validate_deployment_receipt(value: dict) -> None:
    """Validate the exact external deployer result without granting authority."""
    try:
        if (type(value) is not dict or set(value) != _DEPLOYMENT_FIELDS
                or value["format"] != DEPLOYMENT_FORMAT
                or not _matches(_HEX, value["invocation_id"])
                or any(not _matches(_SHA, value[key]) for key in
                       ("source_sha", "expected_source_sha", "live_source_sha"))
                or value["source_sha"] != value["expected_source_sha"]
                or value["source_sha"] != value["live_source_sha"]
                or (value["backup_source_sha"] is not None
                    and not _matches(_SHA, value["backup_source_sha"]))
                or any(not _digest(value[key]) for key in ("code_sha256", "backup_code_sha256"))
                or any(not _matches(_IDENTIFIER, value[key]) for key in
                       ("original_revision_id", "upload_revision_id", "verified_revision_id",
                        "distribution_id", "invalidation_id"))
                or not _matches(_LAST_MODIFIED, value["last_modified"])
                or not _matches(_HEX, value["configuration_sha256"])
                or value["account_id"] != "839000214942" or value["region"] != "us-east-1"
                or value["function_name"] != "guardoath-site" or value["stack_name"] != "guardoath-site"
                or not _timestamp(value["observed_at"])
                or value["last_update_status"] != "Successful"
                or value["release_source_parity_verified"] is not True
                or value["dashboard_ui_matches_local_source"] is not True
                or value["production_live_acceptance"] is not False
                or type(value["automatic_rollback_owned"]) is not bool
                or value["automatic_rollback_owned"] !=
                   (value["upload_revision_id"] == value["verified_revision_id"])
                or type(value["expected_asset_count"]) is not int
                or not 13 <= value["expected_asset_count"] <= 100000
                or type(value["verified_asset_count"]) is not int
                or value["verified_asset_count"] != value["expected_asset_count"]):
            raise ValueError()
    except Exception:
        raise PublicationUnavailable("Deployment receipt cannot be verified") from None


def _validate_runs(runs, candidate: str, evidence: str) -> None:
    if (type(runs) is not list or len(runs) != len(REQUIRED_WORKFLOWS)
            or [run.get("workflow") for run in runs if type(run) is dict] != sorted(REQUIRED_WORKFLOWS)):
        raise ValueError()
    run_ids, job_ids = set(), set()
    for run in runs:
        if (set(run) != {"workflow", "run_id", "run_attempt", "head_sha", "status", "conclusion", "jobs"}
                or not _positive(run["run_id"]) or run["run_id"] in run_ids
                or not _positive(run["run_attempt"]) or run["head_sha"] != candidate
                or run["status"] != "completed" or run["conclusion"] != "success"
                or type(run["jobs"]) is not list
                or [job.get("name") for job in run["jobs"] if type(job) is dict]
                   != list(REQUIRED_WORKFLOWS[run["workflow"]])):
            raise ValueError()
        run_ids.add(run["run_id"])
        for job in run["jobs"]:
            if (set(job) != {"job_id", "name", "head_sha", "status", "conclusion"}
                    or not _positive(job["job_id"]) or job["job_id"] in job_ids
                    or job["head_sha"] != candidate or job["status"] != "completed"
                    or job["conclusion"] != "success"):
                raise ValueError()
            job_ids.add(job["job_id"])
    if len(job_ids) != 25 or evidence != document_hash({
            "repository": REPOSITORY, "candidate_source_sha": candidate, "runs": runs}):
        raise ValueError()


class CatalogPublicationLifecycle:
    """Private adapter used by the bound v1 state facade."""
    ATTRIBUTE = "catalog_publication"

    def __init__(self, state: DynamoCatalogPublicationState):
        if not isinstance(state, DynamoCatalogPublicationState):
            raise ValueError("Bound catalog source state is required")
        self.base = state
        self._key = hmac.new(state._key, CONTRACT.encode("ascii"), hashlib.sha256).digest()

    @staticmethod
    def row_id(kind: str, request_id: str) -> str:
        if type(kind) is not str or kind not in ("control", *_KINDS) or not _matches(_HEX, request_id):
            raise PublicationUnavailable() from None
        return CONTRACT + ":" + kind + ":" + request_id

    def _validate(self, value):
        try:
            if (type(value) is not dict or type(value.get("version")) is not int
                    or value["version"] != 1 or value["contract"] != CONTRACT
                    or not _matches(_HEX, value["signature"])
                    or value["catalog_id"] != CATALOG_ID or value["repository"] != REPOSITORY
                    or any(not _matches(_HEX, value[key]) for key in (
                        "request_id", "intent_hash", "prepared_hash", "history_sha256",
                        "candidate_metadata_sha256", "global_control_hash", "invocation_id"))
                    or any(not _matches(_SHA, value[key]) for key in ("base_source_sha", "candidate_source_sha"))
                    or value["base_source_sha"] == value["candidate_source_sha"]
                    or not _matches(_VERSION, value["candidate_registry_version"])):
                raise ValueError()
            kind = value["kind"]
            if kind == "control":
                if (set(value) != _ENVELOPE | _BINDINGS | {"state", "revision", "receipts", "invocation_id"}
                        or value["state"] not in _STATES or type(value["revision"]) is not int
                        or value["revision"] != _STATES[value["state"]][0]
                        or type(value["receipts"]) is not dict
                        or set(value["receipts"]) != set(_STATES[value["state"]][1])
                        or any(not _matches(_HEX, item) for item in value["receipts"].values())):
                    raise ValueError()
            elif kind in _KINDS:
                if (set(value) != _ENVELOPE | _BINDINGS | _RECEIPT | _EXTRA[kind]
                        or value["status"] != _STATUS[kind]
                        or not _timestamp(value["recorded_at"])
                        or not _matches(_SHA, value["candidate_tree_sha"])
                        or not _matches(_HEX, value["previous_control_hash"])
                        or type(value["receipt_hashes"]) is not dict
                        or set(value["receipt_hashes"]) != set(_KINDS[:_KINDS.index(kind)])
                        or any(not _matches(_HEX, item) for item in value["receipt_hashes"].values())):
                    raise ValueError()
                if kind == "remote" and value["remote_head_sha"] != value["candidate_source_sha"]:
                    raise ValueError()
                if kind == "ci":
                    _validate_runs(value["runs"], value["candidate_source_sha"], value["evidence_sha256"])
                if kind == "deployment":
                    validate_deployment_receipt(value["receipt"])
                    if (value["receipt"]["invocation_id"] != value["invocation_id"]
                            or value["receipt"]["source_sha"] != value["candidate_source_sha"]
                            or value["receipt"]["observed_at"] != value["recorded_at"]):
                        raise ValueError()
                if kind == "completion" and (
                        value["remote_head_sha"] != value["candidate_source_sha"]
                        or value["verified_source_sha"] != value["candidate_source_sha"]
                        or not _digest(value["verified_code_sha256"])
                        or not _matches(_IDENTIFIER, value["verified_revision_id"])
                        or not _matches(_HEX, value["configuration_sha256"])
                        or not _matches(_LAST_MODIFIED, value["last_modified"])
                        or value["production_live_acceptance"] is not False):
                    raise ValueError()
            else:
                raise ValueError()
            if len(_canonical(value).encode("ascii")) > MAX_DOCUMENT_BYTES:
                raise ValueError()
        except Exception:
            raise PublicationUnavailable("Catalog lifecycle evidence unavailable") from None

    def _seal(self, value):
        try:
            unsigned = strict_catalog_json(_canonical({k: v for k, v in value.items() if k != "signature"}))
            signed = {**unsigned, "signature": hmac.new(
                self._key, _canonical(unsigned).encode("ascii"), hashlib.sha256).hexdigest()}
            self._validate(signed)
            return signed
        except Exception:
            raise PublicationUnavailable() from None

    def _verified(self, value, kind):
        self._validate(value)
        if value["kind"] != kind or not hmac.compare_digest(value["signature"], self._seal(value)["signature"]):
            raise PublicationUnavailable() from None

    def _read(self, kind, request_id):
        try:
            row_id = self.row_id(kind, request_id)
            response = self.base.client.get_item(TableName=self.base.table_name,
                Key={"event_id": {"S": row_id}}, ConsistentRead=True)
            self.base._acknowledged(response)
            if "Item" not in response:
                return None
            item = response["Item"]
            if (type(item) is not dict or set(item) != {"event_id", self.ATTRIBUTE}
                    or item["event_id"] != {"S": row_id}
                    or type(item[self.ATTRIBUTE]) is not dict or set(item[self.ATTRIBUTE]) != {"S"}):
                raise ValueError()
            raw = item[self.ATTRIBUTE]["S"]
            if type(raw) is not str or len(raw.encode("utf-8")) > MAX_DOCUMENT_BYTES:
                raise ValueError()
            value = strict_catalog_json(raw)
            self._verified(value, kind)
            if value["request_id"] != request_id or _canonical(value) != raw:
                raise ValueError()
            return value
        except Exception:
            raise PublicationUnavailable() from None

    def _source_control(self, intent):
        return self.base._seal({"version": 1, "contract": "catalog-publication-v1",
            "kind": "control", "request_id": intent["request_id"], "catalog_id": CATALOG_ID,
            "intent_hash": document_hash(intent), "state": "SOURCE_PREPARED", "revision": 3})

    def _bindings(self, history):
        intent, prepared = history["intent"], history["prepared"]
        source_control = self._source_control(intent)
        self.base._links(source_control, intent, prepared)
        return {"request_id": intent["request_id"], "catalog_id": CATALOG_ID,
                "intent_hash": document_hash(intent), "prepared_hash": document_hash(prepared),
                **{key: prepared[key] for key in ("base_source_sha", "candidate_source_sha",
                    "history_sha256", "candidate_metadata_sha256", "candidate_registry_version")},
                "repository": REPOSITORY, "global_control_hash": document_hash(source_control)}

    def _control(self, history, state, invocation_id):
        return self._seal({"version": 1, "contract": CONTRACT, "kind": "control",
            **self._bindings(history), "state": state, "revision": _STATES[state][0],
            "invocation_id": invocation_id,
            "receipts": {kind: document_hash(history[kind]) for kind in _STATES[state][1]}})

    def _check_chain(self, history):
        control = history["control"]
        present = {kind for kind in _KINDS if history[kind] is not None}
        if control is None:
            if present:
                raise PublicationUnavailable() from None
            return
        bindings = self._bindings(history)
        if present != set(_STATES[control["state"]][1]):
            raise PublicationUnavailable() from None
        for value in (control, *(history[kind] for kind in _KINDS if kind in present)):
            if any(value[key] != expected for key, expected in bindings.items()):
                raise PublicationUnavailable() from None
        if control["receipts"] != {kind: document_hash(history[kind]) for kind in present}:
            raise PublicationUnavailable() from None
        previous_time = history["prepared"]["recorded_at"]
        for kind, previous_state in (
                ("remote", "PUBLISHING"), ("ci", "REMOTE_PUBLISHED"),
                ("deployment", "DEPLOYING"), ("completion", "DEPLOYED")):
            value = history[kind]
            if value is None:
                continue
            if (value["receipt_hashes"] != {key: document_hash(history[key])
                    for key in _KINDS[:_KINDS.index(kind)]}
                    or value["previous_control_hash"] != document_hash(
                        self._control(history, previous_state, value["invocation_id"]))
                    or value["recorded_at"] < previous_time
                    or value["candidate_tree_sha"] != history["remote"]["candidate_tree_sha"]):
                raise PublicationUnavailable() from None
            previous_time = value["recorded_at"]
        if history["ci"] is not None and history["ci"]["invocation_id"] != history["remote"]["invocation_id"]:
            raise PublicationUnavailable() from None
        if control["state"] in ("REMOTE_PUBLISHED", "CI_VERIFIED") and (
                control["invocation_id"] != history["remote"]["invocation_id"]):
            raise PublicationUnavailable() from None
        if control["state"] in ("DEPLOYED", "VERIFIED") and (
                control["invocation_id"] != history["deployment"]["invocation_id"]):
            raise PublicationUnavailable() from None
        completion = history["completion"]
        if completion is not None:
            deployed = history["deployment"]
            if (completion["invocation_id"] != deployed["invocation_id"]
                    or any(completion[key] != deployed["receipt"][source] for key, source in (
                        ("verified_source_sha", "source_sha"), ("verified_code_sha256", "code_sha256"),
                        ("verified_revision_id", "verified_revision_id"),
                        ("configuration_sha256", "configuration_sha256"), ("last_modified", "last_modified")))):
                raise PublicationUnavailable() from None

    def history(self, request_id):
        global_control = self.base.read_control()
        history = {"intent": self.base._read("intent", request_id),
                   "prepared": self.base._read("prepared", request_id),
                   "control": self._read("control", request_id),
                   **{kind: self._read(kind, request_id) for kind in _KINDS}}
        intent, prepared = history["intent"], history["prepared"]
        if intent is None:
            if any(value is not None for key, value in history.items() if key != "intent"):
                raise PublicationUnavailable() from None
            return history
        if global_control is None:
            raise PublicationUnavailable() from None
        current = global_control["request_id"] == request_id
        if current:
            self.base._links(global_control, intent, prepared)
        elif prepared is None:
            raise PublicationUnavailable() from None
        if prepared is None:
            if any(history[key] is not None for key in ("control", *_KINDS)):
                raise PublicationUnavailable() from None
        else:
            self._bindings(history)
            self._check_chain(history)
        if not current and history["completion"] is None:
            raise PublicationUnavailable() from None
        return history

    def condition(self, value):
        return {"TableName": self.base.table_name,
                "Key": {"event_id": {"S": self.row_id(value["kind"], value["request_id"])}},
                "ConditionExpression": "attribute_exists(event_id) AND #control = :original",
                "ExpressionAttributeNames": {"#control": self.ATTRIBUTE},
                "ExpressionAttributeValues": {":original": {"S": _canonical(value)}}}

    def _put(self, value, previous=None):
        request = {"TableName": self.base.table_name,
            "Item": {"event_id": {"S": self.row_id(value["kind"], value["request_id"])},
                     self.ATTRIBUTE: {"S": _canonical(value)}},
            "ConditionExpression": "attribute_not_exists(event_id)"}
        if previous is not None:
            condition = self.condition(previous)
            request.update({key: condition[key] for key in
                ("ConditionExpression", "ExpressionAttributeNames", "ExpressionAttributeValues")})
        return request

    def _write(self, history, previous, receipt=None):
        self.base._require_writer()
        self._check_chain(history)
        control = history["control"]
        global_control = self.base.read_control()
        if (global_control is None or global_control["request_id"] != control["request_id"]
                or document_hash(global_control) != control["global_control_hash"]):
            raise PublicationConflict() from None
        condition = {"TableName": self.base.table_name,
            "Key": {"event_id": {"S": self.base.row_id("control", CATALOG_ID)}},
            "ConditionExpression": "attribute_exists(event_id) AND #control = :original",
            "ExpressionAttributeNames": {"#control": self.ATTRIBUTE},
            "ExpressionAttributeValues": {":original": {"S": _canonical(global_control)}}}
        writes = [{"ConditionCheck": condition}, {"Put": self._put(control, previous)}]
        if receipt is not None:
            writes.append({"Put": self._put(receipt)})
        try:
            self.base._acknowledged(self.base.client.transact_write_items(TransactItems=writes))
        except Exception:
            # The claim or result may have committed. Never obtain authority by
            # reading it back and never repeat an uncertain external operation.
            raise PublicationUnavailable() from None
        if self._read("control", control["request_id"]) != control:
            raise PublicationUnavailable() from None
        if receipt is not None and self._read(receipt["kind"], receipt["request_id"]) != receipt:
            raise PublicationUnavailable() from None

    def _input(self, kind, fields):
        if type(fields) is not dict or set(fields) != _FIELDS[kind]:
            raise PublicationUnavailable() from None

    def _record(self, history, kind, fields, claim):
        extras = dict(fields)
        if kind == "deployment":
            extras["recorded_at"] = fields["receipt"]["observed_at"]
        if kind == "completion":
            extras["production_live_acceptance"] = False
        return self._seal({"version": 1, "contract": CONTRACT, "kind": kind,
            **self._bindings(history), "status": _STATUS[kind], **extras,
            "invocation_id": claim["invocation_id"], "previous_control_hash": document_hash(claim),
            "receipt_hashes": {key: document_hash(history[key]) for key in _KINDS[:_KINDS.index(kind)]},
            "candidate_tree_sha": (fields["candidate_tree_sha"] if kind == "remote"
                                   else history["remote"]["candidate_tree_sha"])})

    def _complete(self, request_id, kind, fields, before, after, claim=None):
        self.base._require_writer()
        self._input(kind, fields)
        if kind == "deployment":
            validate_deployment_receipt(fields["receipt"])
        history = self.history(request_id)
        if history["prepared"] is None:
            raise PublicationConflict() from None
        existing = history[kind]
        if claim is not None:
            self._verified(claim, "control")
            if claim["request_id"] != request_id or claim["state"] != before:
                raise PublicationUnavailable() from None
        if existing is not None:
            if (any(existing[key] != value for key, value in fields.items())
                    or (claim is not None and existing["previous_control_hash"] != document_hash(claim))):
                raise PublicationConflict() from None
            return existing
        current = history["control"]
        if (current is None or current["state"] != before
                or (claim is not None and current != claim)):
            raise PublicationConflict() from None
        receipt = self._record(history, kind, fields, current)
        changed = {**history, kind: receipt}
        changed["control"] = self._control(changed, after, current["invocation_id"])
        self._write(changed, current, receipt)
        return receipt

    def claim_remote(self, request_id):
        self.base._require_writer()
        history = self.history(request_id)
        if history["prepared"] is None or history["control"] is not None:
            raise PublicationConflict() from None
        claimed = self._control(history, "PUBLISHING", secrets.token_hex(32))
        self._write({**history, "control": claimed}, None)
        return claimed

    def complete_remote(self, request_id, claim, fields):
        return self._complete(request_id, "remote", fields, "PUBLISHING", "REMOTE_PUBLISHED", claim)

    def record_ci(self, request_id, fields):
        return self._complete(request_id, "ci", fields, "REMOTE_PUBLISHED", "CI_VERIFIED")

    def claim_deployment(self, request_id):
        self.base._require_writer()
        history = self.history(request_id)
        current = history["control"]
        if current is None or current["state"] != "CI_VERIFIED":
            raise PublicationConflict() from None
        claimed = self._control(history, "DEPLOYING", secrets.token_hex(32))
        self._write({**history, "control": claimed}, current)
        return claimed

    def complete_deployment(self, request_id, claim, fields):
        return self._complete(request_id, "deployment", fields, "DEPLOYING", "DEPLOYED", claim)

    def complete_release(self, request_id, fields):
        return self._complete(request_id, "completion", fields, "DEPLOYED", "VERIFIED")
