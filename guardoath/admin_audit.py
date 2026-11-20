"""Owner-only bounded DynamoDB lookup of an immutable administrative audit receipt."""
from __future__ import annotations
import re

EVENT = re.compile(r"^[a-f0-9]{64}$")
TENANT = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
SUBJECT = re.compile(r"^[A-Za-z0-9_-]{12,128}$")
RULE = re.compile(r"^[A-Za-z0-9_.:-]{3,100}$")
UTC = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
ACTIONS = frozenset({"off", "allow", "flag", "block", "same_as_check"})
FIELDS = ("event_id", "tenant_id", "actor_sub", "operation", "when_utc", "reason",
          "prior_revision", "new_revision", "old_status", "new_status",
          "rule_id", "stage", "old_action", "new_action", "old_limit", "new_limit")


class AdminAuditUnavailable(Exception):
    """Provider failures and invalid rows are never returned to clients."""


class AdminAuditReader:
    def __init__(self, ddb, audit_table: str):
        if (not callable(getattr(ddb, "get_item", None)) or
                not isinstance(audit_table, str) or
                not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", audit_table)):
            raise ValueError("Separate audit table and trusted DynamoDB client required")
        self.ddb, self.audit_table = ddb, audit_table

    def read(self, event_id: str) -> dict | None:
        if not isinstance(event_id, str) or not EVENT.fullmatch(event_id):
            raise ValueError("Invalid audit event ID")
        try:
            response = self.ddb.get_item(
                TableName=self.audit_table, Key={"event_id": {"S": event_id}},
                ConsistentRead=True,
                ProjectionExpression=",".join("#" + k for k in FIELDS),
                ExpressionAttributeNames={"#" + k: k for k in FIELDS})
            if not isinstance(response, dict):
                raise AdminAuditUnavailable("Audit read unavailable")
            item = response.get("Item")
            if item is None:
                return None
            if not isinstance(item, dict):
                raise AdminAuditUnavailable("Invalid audit receipt")

            def string(key):
                value = item.get(key)
                return value.get("S") if isinstance(value, dict) and isinstance(value.get("S"), str) else None

            def number(key):
                value = item.get(key)
                raw = value.get("N") if isinstance(value, dict) else None
                if not isinstance(raw, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", raw):
                    raise AdminAuditUnavailable("Invalid numeric audit field")
                return int(raw)

            tenant, actor, operation, when, reason = (string(k) for k in
                ("tenant_id", "actor_sub", "operation", "when_utc", "reason"))
            previous, revision = number("prior_revision"), number("new_revision")
            if (string("event_id") != event_id or not tenant or not TENANT.fullmatch(tenant)
                    or not actor or not SUBJECT.fullmatch(actor)
                    or operation not in ("tenant.status", "tenant.rule_action", "tenant.allowance_cap")
                    or not when or not UTC.fullmatch(when)
                    or not reason or not 10 <= len(reason) <= 240
                    or previous < 1 or revision != previous + 1):
                raise AdminAuditUnavailable("Invalid audit receipt")
            if operation == "tenant.status":
                before, after = string("old_status"), string("new_status")
                if before not in ("active", "disabled") or after not in ("active", "disabled") or before == after:
                    raise AdminAuditUnavailable("Invalid status receipt")
                change = {"field": "status", "before": before, "after": after}
            elif operation == "tenant.rule_action":
                before, after = string("old_action"), string("new_action")
                rule, stage = string("rule_id"), string("stage")
                if (before not in ACTIONS or after not in ACTIONS or before == after
                        or not rule or not RULE.fullmatch(rule) or stage not in ("input", "output")):
                    raise AdminAuditUnavailable("Invalid rule receipt")
                change = {"field": "rule_action", "rule_id": rule, "stage": stage,
                          "before": before, "after": after}
            else:
                before, after = number("old_limit"), number("new_limit")
                if after > before or before > 1000000000:
                    raise AdminAuditUnavailable("Invalid cost cap receipt")
                change = {"field": "monthly_allowance", "before": before, "after": after}
            return {"status": "ok", "source": "persistent DynamoDB administrative audit receipt",
                    "event_id": event_id, "tenant_id": tenant, "actor_sub": actor,
                    "operation": operation, "when_utc": when, "reason": reason,
                    "prior_revision": previous, "new_revision": revision, "change": change}
        except AdminAuditUnavailable:
            raise
        except Exception as exc:
            raise AdminAuditUnavailable("Administrative audit read unavailable") from exc
