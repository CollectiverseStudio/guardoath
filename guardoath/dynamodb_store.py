"""Tenant records in Amazon DynamoDB: the hosted store. It follows the same rules as MemoryTenantStore.

One item per tenant record, keyed by tenant_id:

- ``record``: the record's fields as JSON, except usage and revision.
- ``revision``: a number. Every change to the record is conditional on it, so a change made from a
  stale copy is refused (TenantConflict) instead of overwriting a newer one.
- ``usage``: a map of UTC month to check count. A check is counted with one conditional update on
  this map only, so counting never overwrites a change to the record and a record change never
  overwrites a count. A tenant whose overage setting is block cannot be counted past its allowance.

Reads are strongly consistent, so a saved change is seen by the next request. The table is created
by the deployment (deploy/aws), not here. This module needs the optional aws dependencies.
"""
from datetime import datetime, timezone
import json
import os

from guardoath.tenant_record import EDITABLE_FIELDS, TenantRecord, usage_month
from guardoath.tenant_store import TenantConflict, TenantNotFound, UsageLimitReached

# Every expression this store sends. The test double in tests/test_dynamodb_store.py implements
# exactly these, and a botocore Stubber test checks every request against the DynamoDB API model.
CREATE_CONDITION = "attribute_not_exists(tenant_id)"
CHANGE_UPDATE = "SET #record = :record, #revision = :next"
CHANGE_CONDITION = "attribute_exists(tenant_id) AND #revision = :expected"
COUNT_UPDATE = "SET #usage.#month = if_not_exists(#usage.#month, :zero) + :one"
COUNT_CONDITION = "attribute_exists(tenant_id)"
COUNT_LIMITED_CONDITION = "attribute_exists(tenant_id) AND (attribute_not_exists(#usage.#month) OR #usage.#month < :limit)"
RELEASE_UPDATE = "SET #usage.#month = #usage.#month - :one"
RELEASE_CONDITION = "#usage.#month > :zero"
CONDITION_FAILED = "ConditionalCheckFailedException"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_json(record: TenantRecord) -> str:
    return json.dumps(record.model_dump(mode="json", exclude={"usage", "revision"}), separators=(",", ":"), sort_keys=True)


def to_item(record: TenantRecord) -> dict:
    return {"tenant_id": {"S": record.tenant_id}, "revision": {"N": str(record.revision)},
            "record": {"S": record_json(record)},
            "usage": {"M": {month: {"N": str(count)} for month, count in record.usage.items()}}}


def from_item(item: dict) -> TenantRecord:
    fields = json.loads(item["record"]["S"])
    usage = {month: int(value["N"]) for month, value in item.get("usage", {}).get("M", {}).items()}
    return TenantRecord.model_validate({**fields, "usage": usage, "revision": int(item["revision"]["N"])})


class DynamoDBTenantStore:
    def __init__(self, table_name: str, client):
        if not table_name:
            raise ValueError("A DynamoDB table name is required")
        self.table_name = table_name
        self._client = client

    @classmethod
    def from_environment(cls):
        table = os.getenv("GUARDOATH_TENANT_TABLE")
        if not table:
            raise ValueError("GUARDOATH_TENANT_STORE=dynamodb requires GUARDOATH_TENANT_TABLE")
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            raise ValueError("Install the optional aws dependencies to use GUARDOATH_TENANT_STORE=dynamodb") from None
        # The normal SDK credential chain (the Lambda role); short timeouts, two attempts at most.
        config = Config(connect_timeout=1, read_timeout=2, retries={"total_max_attempts": 2, "mode": "standard"})
        return cls(table, boto3.client("dynamodb", config=config))

    def _failed(self, error) -> bool:
        return getattr(error, "response", {}).get("Error", {}).get("Code") == CONDITION_FAILED

    def get(self, tenant_id: str) -> TenantRecord | None:
        item = self._client.get_item(TableName=self.table_name, Key={"tenant_id": {"S": tenant_id}},
                                     ConsistentRead=True).get("Item")
        return from_item(item) if item else None

    def tenant_ids(self) -> tuple[str, ...]:
        ids, start = [], None
        while True:
            page = self._client.scan(TableName=self.table_name, ProjectionExpression="tenant_id",
                                     ConsistentRead=True, **({"ExclusiveStartKey": start} if start else {}))
            ids.extend(item["tenant_id"]["S"] for item in page.get("Items", []))
            start = page.get("LastEvaluatedKey")
            if not start:
                return tuple(sorted(ids))

    def create(self, record: TenantRecord) -> TenantRecord:
        stored = record.model_copy(update={"revision": 1, "updated_at": _now()})
        try:
            self._client.put_item(TableName=self.table_name, Item=to_item(stored), ConditionExpression=CREATE_CONDITION)
        except Exception as error:
            if self._failed(error):
                raise TenantConflict("Tenant already exists") from None
            raise
        return stored

    def _replace(self, tenant_id: str, current: TenantRecord, updated: TenantRecord) -> TenantRecord:
        """Write the record fields of ``updated`` if the stored revision is still current's."""
        try:
            self._client.update_item(
                TableName=self.table_name, Key={"tenant_id": {"S": tenant_id}},
                UpdateExpression=CHANGE_UPDATE, ConditionExpression=CHANGE_CONDITION,
                ExpressionAttributeNames={"#record": "record", "#revision": "revision"},
                ExpressionAttributeValues={":record": {"S": record_json(updated)}, ":next": {"N": str(updated.revision)},
                                           ":expected": {"N": str(current.revision)}})
        except Exception as error:
            if not self._failed(error):
                raise
            if self.get(tenant_id) is None:
                raise TenantNotFound(tenant_id) from None
            raise TenantConflict("The tenant record changed; reload it and try again") from None
        # Usage is not part of the write; return it as the stored item has it.
        return self.get(tenant_id) or updated

    def update_checks(self, tenant_id: str, changes: dict, expected_revision: int) -> TenantRecord:
        """Change check fields, check settings and the default profile only."""
        unknown = set(changes) - EDITABLE_FIELDS
        if unknown:
            raise ValueError("Only check fields and check settings can be changed: " + ", ".join(sorted(unknown)))
        current = self.get(tenant_id)
        if current is None:
            raise TenantNotFound(tenant_id)
        if current.revision != expected_revision:
            raise TenantConflict("The tenant record changed; reload it and try again")
        updated = TenantRecord.model_validate({**current.model_dump(), **changes,
                                               "revision": current.revision + 1, "updated_at": _now()})
        return self._replace(tenant_id, current, updated)

    def set_key_hashes(self, tenant_id: str, key_hashes: tuple[str, ...]) -> TenantRecord:
        current = self.get(tenant_id)
        if current is None:
            raise TenantNotFound(tenant_id)
        updated = TenantRecord.model_validate({**current.model_dump(), "key_hashes": tuple(key_hashes),
                                               "revision": current.revision + 1, "updated_at": _now()})
        return self._replace(tenant_id, current, updated)

    def count_check(self, tenant_id: str, month: str | None = None) -> tuple[int, bool]:
        """Count one check for the month. Returns (used, over_allowance). Same rules as the memory store."""
        month = month or usage_month()
        current = self.get(tenant_id)
        if current is None:
            raise TenantNotFound(tenant_id)
        limited = current.overage == "block"
        if limited and current.monthly_allowance == 0:
            # The update condition lets a month that is not on the record yet through; with no
            # allowance at all there is nothing to count.
            raise UsageLimitReached("Monthly check limit reached")
        values = {":zero": {"N": "0"}, ":one": {"N": "1"}}
        if limited:
            values[":limit"] = {"N": str(current.monthly_allowance)}
        try:
            response = self._client.update_item(
                TableName=self.table_name, Key={"tenant_id": {"S": tenant_id}},
                UpdateExpression=COUNT_UPDATE,
                ConditionExpression=COUNT_LIMITED_CONDITION if limited else COUNT_CONDITION,
                ExpressionAttributeNames={"#usage": "usage", "#month": month},
                ExpressionAttributeValues=values, ReturnValues="UPDATED_NEW")
        except Exception as error:
            if not self._failed(error):
                raise
            if self.get(tenant_id) is None:
                raise TenantNotFound(tenant_id) from None
            raise UsageLimitReached("Monthly check limit reached") from None
        used = int(response["Attributes"]["usage"]["M"][month]["N"])
        return used, used > current.monthly_allowance

    def release_check(self, tenant_id: str, month: str) -> None:
        """Give back a counted check whose request did not produce a result."""
        try:
            self._client.update_item(
                TableName=self.table_name, Key={"tenant_id": {"S": tenant_id}},
                UpdateExpression=RELEASE_UPDATE, ConditionExpression=RELEASE_CONDITION,
                ExpressionAttributeNames={"#usage": "usage", "#month": month},
                ExpressionAttributeValues={":zero": {"N": "0"}, ":one": {"N": "1"}})
        except Exception as error:
            if not self._failed(error):
                raise


def ensure_demo_records(store, records) -> None:
    """Create any demo record that is missing, and give an existing one its current key hash.
    Existing check fields, settings and usage are kept."""
    for record in records:
        current = store.get(record.tenant_id)
        if current is None:
            try:
                store.create(record)
            except TenantConflict:
                current = store.get(record.tenant_id)
        if current is not None and current.key_hashes != record.key_hashes:
            store.set_key_hashes(record.tenant_id, record.key_hashes)
