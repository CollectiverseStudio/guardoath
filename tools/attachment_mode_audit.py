"""Read-only inventory of persisted attachment modes in GuardOath DynamoDB.

Run from repository root with an authorized AWS profile:
    python -m tools.attachment_mode_audit --profile collectiverse-prod

This tool intentionally DOES NOT change records, inspect check payloads, or print
tenant names, secrets, API keys or full DynamoDB record JSON.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json


FIELDS = ("input_attachment_csv_mode", "output_attachment_csv_mode")
CATEGORIES = ("missing", "auto", "off", "invalid")


class AuditIncompleteError(ValueError):
    """The scan cannot establish a reliable complete tenant-policy inventory."""


def _reject_duplicate_json_keys(pairs):
    """Never silently discard an earlier stored mode (including nested ambiguity)."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key in stored tenant record")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(_value):
    """Reject Python JSON decoder extensions (NaN/Infinity) in any nested field."""
    raise ValueError("Nonfinite JSON constant in stored tenant record")


# Stored DynamoDB JSON has a bounded size but CPython's decoder nesting
# tolerance varies by interpreter/version. Never certify a pathological record
# as Auto/Off on one platform and unparseable on another.
MAX_AUDIT_JSON_NESTING = 128


def _reject_excessive_json_nesting(raw: str) -> None:
    """Bound JSON container nesting without parsing or logging tenant content.

    Brackets inside quoted strings (including escaped quotes/backslashes) are
    data, not structure. A malformed document is still rejected by json.loads.
    """
    depth = 0
    quoted = escaped = False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_AUDIT_JSON_NESTING:
                raise ValueError("Stored tenant JSON exceeds nesting limit")
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("Stored tenant JSON has unbalanced nesting")


def classify_record(raw: str) -> tuple[str, str]:
    """Classify stored fields, never a model with defaults already applied."""
    _reject_excessive_json_nesting(raw)
    record = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys,
                        parse_constant=_reject_nonfinite_json_constant)
    if not isinstance(record, dict):
        raise ValueError("Stored record must be an object")
    return tuple("missing" if field not in record else
                 record[field] if record[field] in ("auto", "off") else "invalid"
                 for field in FIELDS)


def audit(client, table_name: str) -> dict:
    """Read only projected record JSON; return counts without identities."""
    if not table_name:
        raise ValueError("A DynamoDB table name is required")
    counts = {stage: Counter({category: 0 for category in CATEGORIES})
              for stage in ("input", "output")}
    total = invalid_json = 0
    cursor = None
    seen_cursors = set()
    while True:
        request = {
            "TableName": table_name,
            "ProjectionExpression": "#record",
            "ExpressionAttributeNames": {"#record": "record"},
            "ConsistentRead": True,
        }
        if cursor is not None:
            request["ExclusiveStartKey"] = cursor
        try:
            response = client.scan(**request)
        except Exception:
            # AWS failures may embed request parameters or tenant keys in
            # exception strings. The audit must fail closed without echoing them.
            raise AuditIncompleteError("DynamoDB scan request did not complete") from None
        # A partially decoded or malformed page must never be treated as an
        # empty page: doing so could incorrectly certify saved Off as absent.
        if not isinstance(response, dict):
            raise AuditIncompleteError("Invalid DynamoDB scan response")
        items = response.get("Items")
        if items is None:
            # DynamoDB can omit Items for a zero-count page.
            if "Items" in response or response.get("Count") != 0:
                raise AuditIncompleteError("Missing DynamoDB scan items")
            items = []
        if not isinstance(items, list):
            raise AuditIncompleteError("Invalid DynamoDB scan items")
        # DynamoDB Scan promises Count on every page, including zero-row pages.
        # Without it, a truncated/degraded response could silently be counted as
        # an empty inventory and hide a persisted attachment inspection Off.
        if "Count" not in response or (
            type(response["Count"]) is not int or response["Count"] != len(items)
        ):
            raise AuditIncompleteError("Missing or inconsistent DynamoDB scan count")
        for item in items:
            total += 1
            try:
                modes = classify_record(item["record"]["S"])
            # Even well-formed but pathologically nested JSON can exhaust the
            # decoder's recursion limit. Count it as corrupt, not a traceback
            # or a fabricated clean Off/Auto inventory.
            except (KeyError, TypeError, ValueError, RecursionError):
                invalid_json += 1
                continue
            for stage, mode in zip(("input", "output"), modes):
                counts[stage][mode] += 1
        cursor = response.get("LastEvaluatedKey")
        if cursor is not None:
            if not isinstance(cursor, dict) or not cursor:
                raise AuditIncompleteError("Invalid DynamoDB scan continuation")
            try:
                # Keep private key values in memory only, never in exceptions
                # or the aggregate report. Reject malformed/nonfinite or
                # interpreter-dependent deeply nested pagination keys.
                cursor_token = json.dumps(cursor, sort_keys=True, allow_nan=False)
                _reject_excessive_json_nesting(cursor_token)
            except (TypeError, ValueError, RecursionError, OverflowError):
                # Original failures can contain customer key material in
                # exception chains. Do not retain or expose the cause.
                raise AuditIncompleteError("Invalid DynamoDB scan continuation") from None
            if cursor_token in seen_cursors:
                raise AuditIncompleteError("Repeated DynamoDB scan continuation")
            seen_cursors.add(cursor_token)
        else:
            break
    # Off requires *provenance* review; corrupt/unreadable stored modes require
    # a separate data-quality review. A count of zero explicit Off values is
    # not evidence of a clean inventory when any record could not be classified.
    invalid_modes = counts["input"]["invalid"] + counts["output"]["invalid"]
    return {
        "total_records": total,
        "unparseable_records": invalid_json,
        "stages": {stage: dict(values) for stage, values in counts.items()},
        "requires_provenance_review": (
            counts["input"]["off"] + counts["output"]["off"] > 0
        ),
        "requires_data_quality_review": invalid_json > 0 or invalid_modes > 0,
        "mutated_records": 0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="collectiverse-prod")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--table", default="guardoath-tenants")
    args = parser.parse_args(argv)
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        parser.error("Install the existing optional AWS dependencies before running this audit")
    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        client = session.client("dynamodb", config=Config(
            connect_timeout=2, read_timeout=5,
            retries={"total_max_attempts": 2, "mode": "standard"},
        ))
    except Exception:
        # Authentication/profile errors may contain credentials, account data
        # or local path details. Never print the underlying exception.
        print(json.dumps({
            "audit_incomplete": True,
            "requires_data_quality_review": True,
            "mutated_records": 0,
            "reason": "aws_client_unavailable",
        }, indent=2, sort_keys=True))
        return 2
    try:
        report = audit(client, args.table)
    except AuditIncompleteError:
        # Partial inventories must not look like a completed zero-Off scan.
        # Do not expose the cursor, tenant identifiers, or customer record.
        print(json.dumps({
            "audit_incomplete": True,
            "requires_data_quality_review": True,
            "mutated_records": 0,
            "reason": "scan_integrity_error",
        }, indent=2, sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    # Neither unreadable policy nor unresolved explicitly saved Off can be
    # certified by an automated release gate. Off remains authoritative and
    # requires separate human provenance review; never mutate it here.
    return 2 if (report["requires_data_quality_review"] or
                 report["requires_provenance_review"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
