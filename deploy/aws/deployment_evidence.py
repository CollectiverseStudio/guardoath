"""Private-input, bounded evidence for an acknowledged Lambda code update.

Lambda can change RevisionId while its acknowledged update changes from
InProgress to Successful. Completion binds the acknowledged LastModified,
archive digest and complete unchanged configuration instead. The observed
revision NEVER becomes authority for a subsequent rollback.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

_SHA = re.compile(r"[a-f0-9]{40}\Z")
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_REVISION = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_MODIFIED = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}(?:[+-]\d{4}|Z)\Z")
_TRANSIENT = frozenset({
    "CodeSha256", "CodeSize", "LastModified", "RevisionId", "LastUpdateStatus",
    "LastUpdateStatusReason", "LastUpdateStatusReasonCode", "State", "StateReason",
    "StateReasonCode", "RuntimeVersionConfig", "ResponseMetadata",
})
_EVIDENCE_FIELDS = frozenset({"code_sha256", "revision_id", "last_update_status",
                              "last_modified", "configuration_sha256"})
RECEIPT_FIELDS = frozenset({
    "format", "invocation_id", "source_sha", "code_sha256", "backup_code_sha256",
    "backup_source_sha", "original_revision_id", "upload_revision_id", "verified_revision_id",
    "last_modified", "configuration_sha256", "account_id", "region", "function_name",
    "stack_name", "distribution_id", "invalidation_id", "observed_at",
    "automatic_rollback_owned", "last_update_status", "release_source_parity_verified",
    "dashboard_ui_matches_local_source", "expected_source_sha", "live_source_sha",
    "verified_asset_count", "expected_asset_count", "production_live_acceptance",
})


def _require(condition):
    if not condition:
        raise ValueError("Deployment evidence refused")


def _matches(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def _digest(value):
    try:
        raw = base64.b64decode(value, validate=True)
        return type(value) is str and len(raw) == 32 and base64.b64encode(raw).decode("ascii") == value
    except (ValueError, TypeError):
        return False


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def configuration_evidence(configuration):
    # PowerShell's JSON decoder coerces ISO timestamps into DateTime values.
    # Decode the original provider JSON here to retain the exact acknowledgement.
    if type(configuration) is str:
        configuration = json.loads(configuration, object_pairs_hook=_unique,
                                   parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    if type(configuration) is dict and "Configuration" in configuration:
        configuration = configuration["Configuration"]
    _require(type(configuration) is dict)
    _require(all(key in configuration for key in (
        "CodeSha256", "RevisionId", "LastUpdateStatus", "LastModified", "FunctionName",
        "FunctionArn", "Runtime", "Handler", "Role", "Timeout", "MemorySize", "Version")))
    _require(_digest(configuration["CodeSha256"]))
    _require(_matches(_REVISION, configuration["RevisionId"]))
    _require(configuration["LastUpdateStatus"] in {"Successful", "InProgress", "Failed"})
    _require(_matches(_MODIFIED, configuration["LastModified"]))
    _require(type(configuration["FunctionName"]) is str and bool(configuration["FunctionName"]))
    _require(type(configuration["FunctionArn"]) is str and configuration["FunctionArn"].startswith("arn:aws:lambda:"))
    _require(type(configuration["Timeout"]) is int and 1 <= configuration["Timeout"] <= 900)
    _require(type(configuration["MemorySize"]) is int and 128 <= configuration["MemorySize"] <= 10240)
    _require(type(configuration.get("Environment", {})) is dict)
    _require("Error" not in configuration.get("Environment", {}))
    # Include every other returned setting, including future settings, nested
    # environment values, role, layers and resource identity. Never expose them.
    stable = {key: value for key, value in configuration.items() if key not in _TRANSIENT}
    digest = hashlib.sha256(_canonical(stable).encode("ascii")).hexdigest()
    return {"code_sha256": configuration["CodeSha256"],
            "revision_id": configuration["RevisionId"],
            "last_update_status": configuration["LastUpdateStatus"],
            "last_modified": configuration["LastModified"], "configuration_sha256": digest}


def _validate_evidence(value):
    _require(type(value) is dict and set(value) == _EVIDENCE_FIELDS)
    _require(_digest(value["code_sha256"]) and _matches(_REVISION, value["revision_id"]))
    _require(_matches(_MODIFIED, value["last_modified"]) and _matches(_HEX, value["configuration_sha256"]))
    _require(value["last_update_status"] in {"Successful", "InProgress", "Failed"})


def verify_completion(value):
    _require(type(value) is dict and set(value) == {
        "acknowledged", "observed", "expected_code_sha256", "original_configuration_sha256"})
    acknowledged = value["acknowledged"]
    _validate_evidence(acknowledged)
    current = configuration_evidence(value["observed"])
    _require(_digest(value["expected_code_sha256"]))
    _require(_matches(_HEX, value["original_configuration_sha256"]))
    _require(current["last_update_status"] == "Successful")
    _require(acknowledged["last_update_status"] in {"Successful", "InProgress"})
    _require(current["code_sha256"] == acknowledged["code_sha256"] == value["expected_code_sha256"])
    _require(current["last_modified"] == acknowledged["last_modified"])
    _require(current["configuration_sha256"] == acknowledged["configuration_sha256"] ==
             value["original_configuration_sha256"])
    return {**current, "upload_revision_id": acknowledged["revision_id"],
            "automatic_rollback_owned": current["revision_id"] == acknowledged["revision_id"]}


def validate_receipt(receipt):
    _require(type(receipt) is dict and set(receipt) == RECEIPT_FIELDS)
    _require(receipt["format"] == "guardoath.catalog-deployment-receipt.v1")
    _require(_matches(_HEX, receipt["invocation_id"]) and _matches(_HEX, receipt["configuration_sha256"]))
    _require(_matches(_SHA, receipt["source_sha"]))
    _require(receipt["expected_source_sha"] == receipt["live_source_sha"] == receipt["source_sha"])
    _require(receipt["backup_source_sha"] is None or _matches(_SHA, receipt["backup_source_sha"]))
    _require(_digest(receipt["code_sha256"]) and _digest(receipt["backup_code_sha256"]))
    _require(all(_matches(_REVISION, receipt[key]) for key in (
        "original_revision_id", "upload_revision_id", "verified_revision_id")))
    _require(receipt["original_revision_id"] != receipt["upload_revision_id"])
    _require(_matches(_MODIFIED, receipt["last_modified"]))
    _require(receipt["account_id"] == "839000214942" and receipt["region"] == "us-east-1")
    _require(receipt["function_name"] == receipt["stack_name"] == "guardoath-site")
    _require(all(type(receipt[key]) is str and re.fullmatch(r"[A-Z0-9]{5,64}", receipt[key])
                 for key in ("distribution_id", "invalidation_id")))
    _require(type(receipt["observed_at"]) is int and 1 <= receipt["observed_at"] <= 253402300799)
    _require(type(receipt["automatic_rollback_owned"]) is bool and receipt["automatic_rollback_owned"] ==
             (receipt["verified_revision_id"] == receipt["upload_revision_id"]))
    _require(receipt["last_update_status"] == "Successful")
    _require(all(receipt[key] is True for key in ("release_source_parity_verified", "dashboard_ui_matches_local_source")))
    _require(receipt["production_live_acceptance"] is False)
    _require(type(receipt["verified_asset_count"]) is int and type(receipt["expected_asset_count"]) is int
             and 13 <= receipt["verified_asset_count"] == receipt["expected_asset_count"] <= 100)
    return receipt


def receipt_destination(path, checkout):
    _require(type(path) is str and type(checkout) is str)
    target, source = Path(path), Path(checkout).resolve(strict=True)
    _require(target.is_absolute() and target.parent.is_dir())
    _require(target.absolute() == target.resolve() and not target.is_symlink() and not target.exists())
    _require(not target.is_relative_to(source))
    # The standalone release worker already uses a private POSIX workspace.
    # Do not pretend mode bits establish a Windows ACL for an automated receipt.
    _require(os.name == "posix")
    info = target.parent.stat()
    _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) & 0o077 == 0)
    return target


def write_receipt(value):
    _require(type(value) is dict and set(value) == {"receipt", "path", "checkout"})
    receipt = validate_receipt(value["receipt"])
    target = receipt_destination(value["path"], value["checkout"])
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(target, flags, 0o600), "w") as output:
        output.write(_canonical(receipt) + "\n")
        output.flush()
        os.fsync(output.fileno())
    return {"receipt_written": True}


def _unique(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value)
        value[key] = item
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "complete", "path", "write"))
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        _require(len(raw) <= 1024 * 1024)
        value = json.loads(raw, object_pairs_hook=_unique,
                           parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        if args.operation == "inspect":
            result = configuration_evidence(value)
        elif args.operation == "complete":
            result = verify_completion(value)
        elif args.operation == "path":
            _require(type(value) is dict and set(value) == {"path", "checkout"})
            receipt_destination(value["path"], value["checkout"])
            result = {"receipt_destination_valid": True}
        else:
            result = write_receipt(value)
        print(_canonical(result))
        return 0
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        # Input can contain all Lambda environment secrets; never echo it.
        print("Deployment evidence refused")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
