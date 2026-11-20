"""Audited administrative tenant suspension, never a subscription override.

Each action is an atomic DynamoDB transaction: conditional tenant revision update
plus insertion of an immutable audit event. Failed audit write means no change.
The account can be disabled; Stripe entitlements and tenant rules are unchanged.
This capability must be explicitly enabled with a separately authorized audit
DynamoDB table and least-privileged IAM permissions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Callable

from guardoath.admin_audit_integrity import seal_audit

TENANT = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
REASON = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,;:_()/#-]{9,239}$")
IDEMPOTENCY = re.compile(r"^[a-zA-Z0-9_-]{16,96}$")


class AdminActionConflict(Exception):
    pass


class AdminActionUnavailable(Exception):
    pass


class AdminTenantActions:
    def __init__(self, ddb, tenant_table: str, audit_table: str, *, audit_key: bytes,
                 clock: Callable[[], datetime] | None = None):
        if (not tenant_table or not audit_table or tenant_table == audit_table or
                not isinstance(audit_key, bytes) or len(audit_key) < 32 or
                not callable(getattr(ddb, "get_item", None)) or
                not callable(getattr(ddb, "transact_write_items", None))):
            raise ValueError("Audited admin action configuration required")
        self.ddb = ddb
        self.tenant_table = tenant_table
        self.audit_table = audit_table
        self.audit_key = audit_key
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def change_status(self, *, tenant_id: str, expected_revision: int, status: str,
                      confirmation: str, reason: str, actor: str, idempotency_key: str) -> dict:
        if (not isinstance(tenant_id, str) or not TENANT.fullmatch(tenant_id) or
                confirmation != tenant_id or status not in ("active", "disabled") or
                type(expected_revision) is not int or expected_revision < 1 or
                not isinstance(reason, str) or not REASON.fullmatch(reason) or
                not isinstance(actor, str) or len(actor) > 128 or len(actor) < 12 or
                not isinstance(idempotency_key, str) or not IDEMPOTENCY.fullmatch(idempotency_key)):
            raise ValueError("Invalid or unconfirmed administrative status change")
        # No audit event should store raw tokens, Stripe bindings or request bodies.
        raw_id = f"{actor}\x00{tenant_id}\x00{idempotency_key}".encode()
        event_id = hmac.new(self.audit_key, raw_id, hashlib.sha256).hexdigest()
        try:
            response = self.ddb.get_item(
                TableName=self.tenant_table, Key={"tenant_id": {"S": tenant_id}},
                ConsistentRead=True)
            item = response.get("Item")
            if not item or int(item["revision"]["N"]) != expected_revision:
                raise AdminActionConflict("Tenant missing or revision changed")
            record = json.loads(item["record"]["S"])
            if (not isinstance(record, dict) or record.get("tenant_id") != tenant_id or
                    record.get("demo") is True or record.get("status") not in ("active", "disabled") or
                    record["status"] == status):
                raise AdminActionConflict("Tenant is not eligible for this action")
            before = record["status"]
            now = self.clock().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            record = {**record, "status": status, "updated_at": now}
            updated_json = json.dumps(record, sort_keys=True, separators=(",", ":"))
            if len(updated_json.encode()) > 350000:
                raise AdminActionUnavailable("Tenant record exceeds safe size")
            audit = {
                "event_id": {"S": event_id}, "tenant_id": {"S": tenant_id},
                "actor_sub": {"S": actor}, "operation": {"S": "tenant.status"},
                "old_status": {"S": before}, "new_status": {"S": status},
                "reason": {"S": reason}, "when_utc": {"S": now},
                "prior_revision": {"N": str(expected_revision)},
                "new_revision": {"N": str(expected_revision+1)},
            }
            self.ddb.transact_write_items(TransactItems=[
                {"Update": {
                    "TableName": self.tenant_table, "Key": {"tenant_id": {"S": tenant_id}},
                    "UpdateExpression": "SET #record = :record, #revision = :next",
                    "ConditionExpression": "attribute_exists(tenant_id) AND #revision = :prior",
                    "ExpressionAttributeNames": {"#record": "record", "#revision": "revision"},
                    "ExpressionAttributeValues": {
                        ":record": {"S": updated_json},
                        ":next": {"N": str(expected_revision+1)},
                        ":prior": {"N": str(expected_revision)},
                    }
                }},
                {"Put": {"TableName": self.audit_table, "Item": seal_audit(audit, self.audit_key),
                         "ConditionExpression": "attribute_not_exists(event_id)"}}
            ])
        except (AdminActionConflict, AdminActionUnavailable):
            raise
        except Exception as exc:
            # Includes DynamoDB transaction conflicts, IAM denial, audit outage.
            # Fail closed and never confirm mutation success.
            raise AdminActionUnavailable("Administrative transaction could not be verified") from exc
        return {"tenant_id": tenant_id, "status": status,
                "revision": expected_revision+1, "audit_event_id": event_id}

    def change_rule_action(self, *, tenant_id: str, expected_revision: int,
                           stage: str, rule_id: str, action: str,
                           confirmation: str, reason: str, actor: str,
                           idempotency_key: str, validate_record,
                           rule_exists) -> dict:
        """Change one catalog rule's tenant-owned stage action with atomic audit.

        The injected validator is GuardOath TenantRecord.model_validate, not
        arbitrary browser-provided JSON. A nonexistent catalog rule is rejected.
        Saved stage-wide Off remains authoritative and is never changed here.
        """
        if (not isinstance(tenant_id, str) or not TENANT.fullmatch(tenant_id) or
                confirmation != tenant_id or type(expected_revision) is not int or
                expected_revision < 1 or stage not in {"input", "output"} or
                not isinstance(rule_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+", rule_id) or
                action not in {"off", "allow", "flag", "block", "same_as_check"} or
                not isinstance(reason, str) or not REASON.fullmatch(reason) or
                not isinstance(actor, str) or not 12 <= len(actor) <= 128 or
                not isinstance(idempotency_key, str) or not IDEMPOTENCY.fullmatch(idempotency_key) or
                not callable(validate_record) or not callable(rule_exists) or
                rule_exists(rule_id) is not True):
            raise ValueError("Invalid or unconfirmed rule change")
        event_id = hmac.new(self.audit_key,
                            (actor + "\0" + tenant_id + "\0" + idempotency_key).encode(),
                            hashlib.sha256).hexdigest()
        try:
            item = self.ddb.get_item(TableName=self.tenant_table,
                                     Key={"tenant_id":{"S":tenant_id}},
                                     ConsistentRead=True).get("Item")
            if not item or int(item["revision"]["N"]) != expected_revision:
                raise AdminActionConflict("Tenant missing or revision changed")
            doc = json.loads(item["record"]["S"])
            if not isinstance(doc, dict) or doc.get("demo") is True or doc.get("tenant_id") != tenant_id:
                raise AdminActionConflict("Tenant cannot be changed")
            field = stage + "_rule_actions"
            previous = doc.get(field) or {}
            if not isinstance(previous, dict):
                raise AdminActionConflict("Stored rule actions invalid")
            before = previous.get(rule_id,"same_as_check")
            if before == action:
                raise AdminActionConflict("Rule action already has this value")
            after = dict(previous)
            if action == "same_as_check":
                after.pop(rule_id,None)
            else:
                after[rule_id] = action
            updated = {**doc, field:after}
            # The full typed GuardOath record decides validity, not this
            # individual field parser. Fail closed on any validation error.
            validated = validate_record({**updated,"revision":expected_revision+1})
            if not callable(getattr(validated,"model_dump",None)):
                raise ValueError("Record validator did not return a typed record")
            # Use original stored schema names wherever validation normalizes
            # legacy records; only the targeted field changes.
            serialized = json.dumps({**doc,field:after,
                                     "updated_at":self.clock().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")},
                                    separators=(",", ":"),sort_keys=True)
            if len(serialized.encode()) > 350000:
                raise AdminActionUnavailable("Tenant record exceeds safe size")
            timestamp = self.clock().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")
            audit = {"event_id":{"S":event_id},"tenant_id":{"S":tenant_id},
                     "actor_sub":{"S":actor},"operation":{"S":"tenant.rule_action"},
                     "rule_id":{"S":rule_id},"stage":{"S":stage},
                     "old_action":{"S":before},"new_action":{"S":action},
                     "prior_revision":{"N":str(expected_revision)},
                     "new_revision":{"N":str(expected_revision+1)},
                     "reason":{"S":reason},"when_utc":{"S":timestamp}}
            self.ddb.transact_write_items(TransactItems=[
                {"Update":{"TableName":self.tenant_table,"Key":{"tenant_id":{"S":tenant_id}},
                           "UpdateExpression":"SET #record = :record, #revision = :next",
                           "ConditionExpression":"attribute_exists(tenant_id) AND #revision = :prior",
                           "ExpressionAttributeNames":{"#record":"record","#revision":"revision"},
                           "ExpressionAttributeValues":{
                               ":record":{"S":serialized},":next":{"N":str(expected_revision+1)},
                               ":prior":{"N":str(expected_revision)}}}},
                {"Put":{"TableName":self.audit_table,"Item":seal_audit(audit,self.audit_key),
                        "ConditionExpression":"attribute_not_exists(event_id)"}},
            ])
        except (AdminActionConflict,AdminActionUnavailable):
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise AdminActionUnavailable("Audited rule change unavailable") from exc
        return {"tenant_id":tenant_id,"revision":expected_revision+1,
                "stage":stage,"rule_id":rule_id,"action":action,"audit_event_id":event_id}

    def cap_monthly_allowance(self, *, tenant_id: str, expected_revision: int,
                              new_limit: int, confirmation: str, reason: str,
                              actor: str, idempotency_key: str,
                              validate_record) -> dict:
        """Lower a tenant's maximum request allowance; cannot grant paid usage.

        Sets overage='block' to make the new cap actually enforceable. This is
        a one-way emergency budget guard, not a substitute for Stripe plan
        reconciliation or general customer billing administration.
        """
        if (not isinstance(tenant_id,str) or not TENANT.fullmatch(tenant_id) or
                confirmation!=tenant_id or type(expected_revision) is not int or expected_revision<1 or
                type(new_limit) is not int or not 0<=new_limit<=1_000_000_000 or
                not isinstance(reason,str) or not REASON.fullmatch(reason) or
                not isinstance(actor,str) or not 12<=len(actor)<=128 or
                not isinstance(idempotency_key,str) or not IDEMPOTENCY.fullmatch(idempotency_key) or
                not callable(validate_record)):
            raise ValueError("Invalid or unconfirmed cost-control change")
        event_id=hmac.new(self.audit_key,(actor+"\0"+tenant_id+"\0"+idempotency_key).encode(),hashlib.sha256).hexdigest()
        try:
            item=self.ddb.get_item(TableName=self.tenant_table,
                                   Key={"tenant_id":{"S":tenant_id}},ConsistentRead=True).get("Item")
            if not item or int(item["revision"]["N"])!=expected_revision:
                raise AdminActionConflict("Tenant missing or revision changed")
            doc=json.loads(item["record"]["S"])
            if not isinstance(doc,dict) or doc.get("tenant_id")!=tenant_id or doc.get("demo") is True:
                raise AdminActionConflict("Tenant not eligible")
            old=doc.get("monthly_allowance")
            if type(old) is not int or new_limit>old or (new_limit==old and doc.get("overage")=="block"):
                raise AdminActionConflict("Cost cap may only reduce current allowance or enforce blocking")
            now=self.clock().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")
            updated={**doc,"monthly_allowance":new_limit,"overage":"block","updated_at":now}
            validated=validate_record({**updated,"revision":expected_revision+1})
            if not callable(getattr(validated,"model_dump",None)):
                raise ValueError("Invalid record validation result")
            body=json.dumps(updated,sort_keys=True,separators=(",",":"))
            if len(body.encode())>350000: raise AdminActionUnavailable("Record exceeds safe size")
            audit={"event_id":{"S":event_id},"tenant_id":{"S":tenant_id},
                   "actor_sub":{"S":actor},"operation":{"S":"tenant.allowance_cap"},
                   "old_limit":{"N":str(old)},"new_limit":{"N":str(new_limit)},
                   "prior_revision":{"N":str(expected_revision)},
                   "new_revision":{"N":str(expected_revision+1)},
                   "reason":{"S":reason},"when_utc":{"S":now}}
            self.ddb.transact_write_items(TransactItems=[
                {"Update":{"TableName":self.tenant_table,"Key":{"tenant_id":{"S":tenant_id}},
                    "UpdateExpression":"SET #record = :record, #revision = :next",
                    "ConditionExpression":"attribute_exists(tenant_id) AND #revision = :prior",
                    "ExpressionAttributeNames":{"#record":"record","#revision":"revision"},
                    "ExpressionAttributeValues":{":record":{"S":body},
                        ":next":{"N":str(expected_revision+1)},
                        ":prior":{"N":str(expected_revision)}}}},
                {"Put":{"TableName":self.audit_table,"Item":seal_audit(audit,self.audit_key),
                        "ConditionExpression":"attribute_not_exists(event_id)"}}
            ])
        except (AdminActionConflict,AdminActionUnavailable):raise
        except ValueError:raise
        except Exception as exc:raise AdminActionUnavailable("Cost cap could not be verified") from exc
        return {"tenant_id":tenant_id,"monthly_allowance":new_limit,
                "overage":"block","revision":expected_revision+1,
                "audit_event_id":event_id}

    def revoke_all_keys(self, *, tenant_id: str, expected_revision: int,
                        confirmation: str, reason: str, actor: str,
                        idempotency_key: str, validate_record) -> dict:
        """Atomically revoke existing tenant application keys, preserving billing and login."""
        if (not isinstance(tenant_id, str) or not TENANT.fullmatch(tenant_id)
                or confirmation != tenant_id
                or type(expected_revision) is not int or expected_revision < 1
                or not isinstance(reason, str) or not REASON.fullmatch(reason)
                or not isinstance(actor, str) or not 12 <= len(actor) <= 128
                or not isinstance(idempotency_key, str) or not IDEMPOTENCY.fullmatch(idempotency_key)
                or not callable(validate_record)):
            raise ValueError("Invalid or unconfirmed application-key revocation")
        event_id = hmac.new(
            self.audit_key, (actor + "\0" + tenant_id + "\0" + idempotency_key).encode(),
            hashlib.sha256).hexdigest()
        try:
            item = self.ddb.get_item(
                TableName=self.tenant_table,
                Key={"tenant_id": {"S": tenant_id}}, ConsistentRead=True).get("Item")
            if not item or int(item["revision"]["N"]) != expected_revision:
                raise AdminActionConflict("Tenant missing or revision changed")
            doc = json.loads(item["record"]["S"])
            if (not isinstance(doc, dict) or doc.get("tenant_id") != tenant_id
                    or doc.get("demo") is True or doc.get("status") not in ("active", "disabled")):
                raise AdminActionConflict("Tenant is not eligible")
            keys = doc.get("key_hashes")
            if (not isinstance(keys, list) or not 1 <= len(keys) <= 2 or
                    any(not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key)
                        for key in keys) or len(set(keys)) != len(keys)):
                raise AdminActionConflict("Tenant has no verified keys to revoke")
            now = self.clock().astimezone(timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z")
            updated = {**doc, "key_hashes": [], "updated_at": now}
            validated = validate_record({**updated, "revision": expected_revision + 1})
            if not callable(getattr(validated, "model_dump", None)):
                raise ValueError("Invalid tenant record validation")
            body = json.dumps(updated, sort_keys=True, separators=(",", ":"))
            if len(body.encode()) > 350000:
                raise AdminActionUnavailable("Tenant record exceeds safe size")
            audit = {
                "event_id": {"S": event_id}, "tenant_id": {"S": tenant_id},
                "actor_sub": {"S": actor}, "operation": {"S": "tenant.keys_revoke"},
                "old_key_count": {"N": str(len(keys))}, "new_key_count": {"N": "0"},
                "prior_revision": {"N": str(expected_revision)},
                "new_revision": {"N": str(expected_revision + 1)},
                "reason": {"S": reason}, "when_utc": {"S": now},
            }
            self.ddb.transact_write_items(TransactItems=[
                {"Update": {
                    "TableName": self.tenant_table,
                    "Key": {"tenant_id": {"S": tenant_id}},
                    "UpdateExpression": "SET #record = :record, #revision = :next",
                    "ConditionExpression": "attribute_exists(tenant_id) AND #revision = :prior",
                    "ExpressionAttributeNames": {"#record": "record", "#revision": "revision"},
                    "ExpressionAttributeValues": {
                        ":record": {"S": body},
                        ":next": {"N": str(expected_revision + 1)},
                        ":prior": {"N": str(expected_revision)},
                    },
                }},
                {"Put": {"TableName": self.audit_table,
                         "Item": seal_audit(audit, self.audit_key),
                         "ConditionExpression": "attribute_not_exists(event_id)"}},
            ])
        except (AdminActionConflict, AdminActionUnavailable, ValueError):
            raise
        except Exception as exc:
            raise AdminActionUnavailable("Key revocation could not be verified") from exc
        return {"tenant_id": tenant_id, "api_key_count": 0,
                "revision": expected_revision + 1, "audit_event_id": event_id}
