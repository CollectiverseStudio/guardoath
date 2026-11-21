"""Owner-only bounded DynamoDB lookup of an immutable administrative audit receipt."""
from __future__ import annotations
from datetime import datetime, timezone
import re
import secrets

from guardoath.admin_audit_integrity import SIGNED_FIELDS, seal_audit, verify_audit

EVENT = re.compile(r"^[a-f0-9]{64}$")
TENANT = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
SUBJECT = re.compile(r"^[A-Za-z0-9_-]{12,128}$")
RULE = re.compile(r"^[A-Za-z0-9_.:-]{3,100}$")
UTC = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
ACTIONS = frozenset({"off", "allow", "flag", "block", "same_as_check"})
FIELDS = (*SIGNED_FIELDS, "integrity_mac")
SENSITIVE_READS = frozenset({
    "cognito.exact_user", "cognito.user_directory", "stripe.subscription", "stripe.invoices", "audit.receipt",
    "tenant.directory", "tenant.detail", "tenant.usage", "catalog.registry", "catalog.preview",
    "admin.overview", "admin.features", "catalog.draft", "cognito.mutation_preflight",
})
READ_REASON = "Owner requested sensitive account inspection"


class AdminAuditUnavailable(Exception):
    """Provider failures and invalid rows are never returned to clients."""


class AdminSensitiveReadAuditor:
    """Append signed, metadata-only read-attempt receipt before sensitive provider reads."""

    def __init__(self, ddb, audit_table: str, *, signing_key: bytes, clock=None):
        if (not callable(getattr(ddb, "put_item", None)) or
                not isinstance(audit_table, str) or
                not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", audit_table) or
                not isinstance(signing_key, bytes) or len(signing_key) < 32):
            raise ValueError("Independent signed audit configuration required")
        self.ddb, self.audit_table, self.signing_key = ddb, audit_table, signing_key
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def log(self, *, actor_sub: str, resource: str, tenant_id: str = "system") -> str:
        if (not isinstance(actor_sub, str) or not SUBJECT.fullmatch(actor_sub) or
                resource not in SENSITIVE_READS or not isinstance(tenant_id, str)
                or not TENANT.fullmatch(tenant_id)):
            raise ValueError("Invalid protected read audit context")
        try:
            now = self.clock()
            if not isinstance(now, datetime) or now.tzinfo is None:
                raise ValueError("Invalid audit time")
            event_id = secrets.token_hex(32)
            item = {
                "event_id": {"S": event_id}, "tenant_id": {"S": tenant_id},
                "actor_sub": {"S": actor_sub}, "operation": {"S": "admin.read"},
                "read_resource": {"S": resource}, "reason": {"S": READ_REASON},
                "when_utc": {"S": now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}}
            self.ddb.put_item(TableName=self.audit_table,
                              Item=seal_audit(item, self.signing_key),
                              ConditionExpression="attribute_not_exists(event_id)")
            return event_id
        except Exception as exc:
            raise AdminAuditUnavailable("Protected administrative read audit unavailable") from exc


class AdminAuditReader:
    def __init__(self, ddb, audit_table: str, *, verify_key: bytes | None = None):
        if (not callable(getattr(ddb, "get_item", None)) or
                not isinstance(audit_table, str) or
                not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", audit_table) or
                (verify_key is not None and (not isinstance(verify_key, bytes) or len(verify_key) < 32))):
            raise ValueError("Separate audit table and trusted DynamoDB client required")
        self.ddb, self.audit_table, self.verify_key = ddb, audit_table, verify_key

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

            integrity_status = "legacy_unverified"
            if "integrity_mac" in item:
                if self.verify_key is None or not verify_audit(item, self.verify_key):
                    raise AdminAuditUnavailable("Audit receipt integrity verification failed")
                integrity_status = "signed_verified"

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
            if (string("event_id") != event_id or not tenant or not TENANT.fullmatch(tenant)
                    or not actor or not SUBJECT.fullmatch(actor)
                    or operation not in ("tenant.status", "tenant.rule_action", "tenant.allowance_cap",
                                         "tenant.keys_revoke", "admin.read",
                                         "cognito.user.status.intent", "cognito.user.status.outcome",
                                         "billing.downgrade")
                    or not when or not UTC.fullmatch(when)
                    or not reason or not 10 <= len(reason) <= 240):
                raise AdminAuditUnavailable("Invalid audit receipt")
            if operation == "admin.read":
                resource = string("read_resource")
                if (integrity_status != "signed_verified" or resource not in SENSITIVE_READS
                        or reason != READ_REASON or "prior_revision" in item
                        or "new_revision" in item):
                    raise AdminAuditUnavailable("Invalid protected read receipt")
                return {"status": "ok", "integrity_status": integrity_status,
                        "source": "persistent DynamoDB administrative audit receipt",
                        "event_id": event_id, "tenant_id": tenant, "actor_sub": actor,
                        "operation": operation, "when_utc": when, "reason": reason,
                        "change": {"field": "protected_read", "resource": resource}}
            if operation in ("cognito.user.status.intent", "cognito.user.status.outcome"):
                before, after, target = string("old_enabled"), string("new_enabled"), string("target_fingerprint")
                related = string("related_event_id")
                if (integrity_status != "signed_verified" or before not in ("true", "false")
                        or after not in ("true", "false") or before == after
                        or not target or not EVENT.fullmatch(target)
                        or "read_resource" in item or "prior_revision" in item or "new_revision" in item
                        or (operation.endswith(".outcome") and (not related or not EVENT.fullmatch(related)))
                        or (operation.endswith(".intent") and "related_event_id" in item)):
                    raise AdminAuditUnavailable("Invalid Cognito status receipt")
                return {"status": "ok", "integrity_status": integrity_status,
                        "source": "persistent DynamoDB administrative audit receipt",
                        "event_id": event_id, "tenant_id": tenant, "actor_sub": actor,
                        "operation": operation, "when_utc": when, "reason": reason,
                        "change": {"field": "cognito_enabled", "before": before == "true",
                                   "after": after == "true", "target_fingerprint": target,
                                   **({"related_event_id": related} if related else {})}}
            previous, revision = number("prior_revision"), number("new_revision")
            if previous < 1 or revision != previous + 1 or "read_resource" in item:
                raise AdminAuditUnavailable("Invalid mutation receipt")
            if operation == "billing.downgrade":
                before, after, provider = (string(k) for k in
                    ("old_subscription_status", "new_subscription_status", "provider_status"))
                old_limit, new_limit = number("old_limit"), number("new_limit")
                mapping = {"past_due": "past_due", "canceled": "canceled",
                           "unpaid": "inactive", "incomplete": "inactive",
                           "incomplete_expired": "inactive", "trialing": "inactive",
                           "paused": "inactive"}
                if (integrity_status != "signed_verified" or before != "active"
                        or provider not in mapping or after != mapping[provider]
                        or not 0 <= old_limit <= 1000000000 or new_limit != 0):
                    raise AdminAuditUnavailable("Invalid billing downgrade receipt")
                change = {"field": "subscription_status", "before": before,
                          "after": after, "provider_status": provider,
                          "allowance_before": old_limit, "allowance_after": new_limit}
            elif operation == "tenant.status":
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
            elif operation == "tenant.allowance_cap":
                before, after = number("old_limit"), number("new_limit")
                if after > before or before > 1000000000:
                    raise AdminAuditUnavailable("Invalid cost cap receipt")
                change = {"field": "monthly_allowance", "before": before, "after": after}
            else:
                before, after = number("old_key_count"), number("new_key_count")
                if before not in (1, 2) or after != 0:
                    raise AdminAuditUnavailable("Invalid key revocation receipt")
                change = {"field": "api_keys", "before_count": before, "after_count": after}
            return {"status": "ok", "integrity_status": integrity_status,
                    "source": "persistent DynamoDB administrative audit receipt",
                    "event_id": event_id, "tenant_id": tenant, "actor_sub": actor,
                    "operation": operation, "when_utc": when, "reason": reason,
                    "prior_revision": previous, "new_revision": revision, "change": change}
        except AdminAuditUnavailable:
            raise
        except Exception as exc:
            raise AdminAuditUnavailable("Administrative audit read unavailable") from exc
