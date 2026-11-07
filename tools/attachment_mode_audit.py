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


def classify_record(raw: str) -> tuple[str, str]:
    """Classify stored fields, never a model with defaults already applied."""
    record = json.loads(raw)
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
    while True:
        request = {
            "TableName": table_name,
            "ProjectionExpression": "#record",
            "ExpressionAttributeNames": {"#record": "record"},
            "ConsistentRead": True,
        }
        if cursor is not None:
            request["ExclusiveStartKey"] = cursor
        response = client.scan(**request)
        for item in response.get("Items", []):
            total += 1
            try:
                modes = classify_record(item["record"]["S"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                invalid_json += 1
                continue
            for stage, mode in zip(("input", "output"), modes):
                counts[stage][mode] += 1
        cursor = response.get("LastEvaluatedKey")
        if cursor is None:
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
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    client = session.client("dynamodb", config=Config(
        connect_timeout=2, read_timeout=5,
        retries={"total_max_attempts": 2, "mode": "standard"},
    ))
    report = audit(client, args.table)
    print(json.dumps(report, indent=2, sort_keys=True))
    # Invalid mode values are not a clean production inventory even when the
    # record itself is valid JSON; use the same nonzero exit as unreadable rows.
    return 2 if report["requires_data_quality_review"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
