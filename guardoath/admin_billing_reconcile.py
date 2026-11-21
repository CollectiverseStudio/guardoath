"""Owner billing remediation: verified Stripe delinquency, atomic signed downgrade only.

No entitlement activation, payment-provider write, or quota increase is possible.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json

from guardoath.admin_actions import TENANT, REASON, IDEMPOTENCY
from guardoath.admin_audit_integrity import seal_audit
from guardoath.admin_billing import AdminSubscriptionReporter, BillingUnavailable


class BillingDowngradeConflict(Exception):
    pass


class BillingDowngradeUnavailable(Exception):
    pass


DOWNGRADE = {"past_due": "past_due", "canceled": "canceled",
             "unpaid": "inactive", "incomplete": "inactive",
             "incomplete_expired": "inactive", "trialing": "inactive",
             "paused": "inactive"}


class AdminBillingDowngrade:
    def __init__(self, ddb, tenant_table, audit_table, *, signing_key, reporter, validate_record, clock=None):
        if (not callable(getattr(ddb, "get_item", None))
                or not callable(getattr(ddb, "transact_write_items", None))
                or not tenant_table or not audit_table or tenant_table == audit_table
                or not isinstance(signing_key, bytes) or len(signing_key) < 32
                or not isinstance(reporter, AdminSubscriptionReporter)
                or not callable(validate_record)):
            raise ValueError("Independent audited Stripe reconciler required")
        self.ddb, self.tenant_table, self.audit_table = ddb, tenant_table, audit_table
        self.key, self.reporter, self.validate_record = signing_key, reporter, validate_record
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def apply(self, *, tenant_id, expected_revision, confirmation, reason, actor, idempotency_key):
        if (not isinstance(tenant_id, str) or not TENANT.fullmatch(tenant_id)
                or type(expected_revision) is not int or expected_revision < 1
                or confirmation != "RECONCILE " + tenant_id
                or not isinstance(reason, str) or not REASON.fullmatch(reason)
                or not isinstance(actor, str) or not 12 <= len(actor) <= 128
                or not isinstance(idempotency_key, str) or not IDEMPOTENCY.fullmatch(idempotency_key)):
            raise ValueError("Invalid or unconfirmed billing reconciliation")
        event_id = hmac.new(self.key,
            ("billing-downgrade\x00" + actor + "\x00" + tenant_id + "\x00" +
             idempotency_key).encode(), hashlib.sha256).hexdigest()
        try:
            reply = self.ddb.get_item(TableName=self.tenant_table,
                Key={"tenant_id": {"S": tenant_id}}, ConsistentRead=True)
            item = reply.get("Item") if isinstance(reply, dict) else None
            if not item or int(item["revision"]["N"]) != expected_revision:
                raise BillingDowngradeConflict("Tenant missing or revision changed")
            record = json.loads(item["record"]["S"])
            if (not isinstance(record, dict) or record.get("tenant_id") != tenant_id
                    or record.get("demo") is not False
                    or record.get("billing_mode") != "stripe"
                    or record.get("subscription_status") != "active"
                    or not isinstance(record.get("payer_id"), str)
                    or not isinstance(record.get("stripe_subscription_id"), str)
                    or type(record.get("monthly_allowance")) is not int
                    or record["monthly_allowance"] < 0):
                raise BillingDowngradeConflict("Saved Stripe entitlement not eligible")
            before = self.validate_record({**record, "revision": expected_revision})
            if not callable(getattr(before, "model_dump", None)):
                raise BillingDowngradeUnavailable("Invalid tenant validator")
            provider = self.reporter.read(before)
            state = provider.get("status") if isinstance(provider, dict) else None
            if (provider.get("verified") is not True or state not in DOWNGRADE
                    or provider.get("provider_active") is not False):
                raise BillingDowngradeConflict("No verified delinquency; no account change")
            next_status = DOWNGRADE[state]
            utc = self.clock().astimezone(timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z")
            next_record = {**record, "subscription_status": next_status,
                "monthly_allowance": 0, "overage": "block", "updated_at": utc}
            validated = self.validate_record({**next_record, "revision": expected_revision + 1})
            if not callable(getattr(validated, "model_dump", None)):
                raise BillingDowngradeUnavailable("Invalid tenant validation")
            body = json.dumps(next_record, sort_keys=True, separators=(",", ":"))
            if len(body.encode()) > 350000:
                raise BillingDowngradeUnavailable("Tenant exceeds safe size")
            receipt = {
                "event_id": {"S": event_id}, "tenant_id": {"S": tenant_id},
                "actor_sub": {"S": actor}, "operation": {"S": "billing.downgrade"},
                "reason": {"S": reason}, "when_utc": {"S": utc},
                "prior_revision": {"N": str(expected_revision)},
                "new_revision": {"N": str(expected_revision+1)},
                "old_subscription_status": {"S": "active"},
                "new_subscription_status": {"S": next_status},
                "provider_status": {"S": state},
                "old_limit": {"N": str(record["monthly_allowance"])},
                "new_limit": {"N": "0"},
            }
            self.ddb.transact_write_items(TransactItems=[
                {"Update": {
                    "TableName": self.tenant_table, "Key": {"tenant_id": {"S": tenant_id}},
                    "UpdateExpression": "SET #record = :record, #revision = :next",
                    "ConditionExpression": "attribute_exists(tenant_id) AND #revision = :prior",
                    "ExpressionAttributeNames": {"#record": "record", "#revision": "revision"},
                    "ExpressionAttributeValues": {":record": {"S": body},
                        ":next": {"N": str(expected_revision+1)},
                        ":prior": {"N": str(expected_revision)}}}},
                {"Put": {"TableName": self.audit_table,
                    "Item": seal_audit(receipt, self.key),
                    "ConditionExpression": "attribute_not_exists(event_id)"}}
            ])
        except (BillingDowngradeConflict, BillingDowngradeUnavailable):
            raise
        except (ValueError, BillingUnavailable) as exc:
            raise BillingDowngradeUnavailable("Provider or tenant validation unavailable") from exc
        except Exception as exc:
            raise BillingDowngradeUnavailable("Audited reconciliation unavailable") from exc
        return {"tenant_id": tenant_id, "revision": expected_revision+1,
                "subscription_status": next_status, "monthly_allowance": 0,
                "overage": "block", "provider_status": state,
                "audit_event_id": event_id}
