"""Offline external-service boundary for real owner API/browser acceptance.

SQLite persists typed DynamoDB fixture items and atomically enforces the exact
conditional requests used by the production services. This is never imported
by GuardOath runtime code and is not an AWS emulator or live provider evidence.
"""
import json
import sqlite3
from threading import RLock

ADMIN_BROWSER_AUDIT_KEY = b"synthetic-admin-browser-audit-key-not-production-0123456789"


class ConditionalFailure(RuntimeError):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class AdminTestDynamo:
    def __init__(self, path=":memory:"):
        self.connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.connection.execute("CREATE TABLE IF NOT EXISTS items (table_name TEXT, item_key TEXT, body TEXT, PRIMARY KEY(table_name,item_key))")
        self.lock = RLock()
        self.fail_audit = False
        self.fail_transaction = False
        self.lose_transaction_response = False
        self.transactions = 0

    def close(self):
        self.connection.close()

    def _key(self, item):
        return item.get("event_id", item.get("tenant_id"))["S"]

    def _get(self, table, key):
        row = self.connection.execute("SELECT body FROM items WHERE table_name=? AND item_key=?", (table, key)).fetchone()
        return json.loads(row[0]) if row else None

    def _save(self, table, item):
        self.connection.execute("INSERT OR REPLACE INTO items VALUES (?,?,?)", (table, self._key(item), json.dumps(item)))

    def seed(self, table, item):
        with self.lock:
            self._save(table, item)

    def get_item(self, *, TableName, Key, **_projection):
        with self.lock:
            item = self._get(TableName, self._key(Key))
            return {"Item": item} if item is not None else {}

    def put_item(self, *, TableName, Item, ConditionExpression):
        assert ConditionExpression in ("attribute_not_exists(event_id)", "attribute_not_exists(tenant_id)")
        with self.lock:
            if self.fail_audit and "event_id" in Item:
                raise RuntimeError("Synthetic audit outage")
            if self._get(TableName, self._key(Item)) is not None:
                raise ConditionalFailure("Synthetic conditional conflict")
            self._save(TableName, Item)
        return {}

    def _update(self, update):
        assert update["UpdateExpression"] == "SET #record = :record, #revision = :next"
        table, key = update["TableName"], self._key(update["Key"])
        old = self._get(table, key)
        values = update["ExpressionAttributeValues"]
        if update["ConditionExpression"] == "attribute_exists(tenant_id) AND #revision = :prior AND #record = :original":
            if old is None or old["revision"] != values[":prior"] or old["record"] != values[":original"]:
                raise ConditionalFailure("Synthetic source race")
        else:
            assert update["ConditionExpression"] == "attribute_exists(tenant_id) AND #revision = :expected"
            if old is None or old["revision"] != values[":expected"]:
                raise ConditionalFailure("Synthetic revision race")
        self._save(table, {**old, "record": values[":record"], "revision": values[":next"]})

    def update_item(self, **request):
        with self.lock:
            self._update(request)
        return {}

    def transact_write_items(self, *, TransactItems):
        assert len(TransactItems) == 2 and set(TransactItems[0]) == {"Update"} and set(TransactItems[1]) == {"Put"}
        with self.lock:
            if self.fail_transaction:
                raise RuntimeError("Synthetic transaction outage")
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self._update(TransactItems[0]["Update"])
                self.put_item(**TransactItems[1]["Put"])
                self.connection.execute("COMMIT")
                self.transactions += 1
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
            if self.lose_transaction_response:
                raise TimeoutError("Synthetic committed response loss")
        return {}

    def scan(self, *, TableName, Limit, ExclusiveStartKey=None, **_projection):
        with self.lock:
            start = self._key(ExclusiveStartKey) if ExclusiveStartKey else ""
            rows = self.connection.execute("SELECT body FROM items WHERE table_name=? AND item_key>? ORDER BY item_key LIMIT ?",
                (TableName, start, Limit + 1)).fetchall()
            items = [json.loads(row[0]) for row in rows[:Limit]]
            return {"Items": items, **({"LastEvaluatedKey": {"tenant_id": items[-1]["tenant_id"]}} if len(rows) > Limit else {})}

    def audit_items(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.connection.execute("SELECT body FROM items WHERE table_name='audit_table'")]
