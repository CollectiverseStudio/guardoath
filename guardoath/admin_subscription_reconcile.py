"""Owner-requested reconciliation from a fresh, independently verified Stripe read.

Only the authoritative tenant is changed. No Stripe financial write, manual
paid toggle, identity change, key creation or customer-policy edit is possible.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json

from guardoath.admin_actions import TENANT, REASON, IDEMPOTENCY
from guardoath.admin_audit import AdminAuditReader, SUBJECT
from guardoath.admin_audit_integrity import seal_audit, verify_audit
from guardoath.admin_billing import AdminSubscriptionReporter
from guardoath.admin_directory import _strict_record_json
from guardoath.subscription_reconciliation import (
    StripeBinding, SubscriptionReconciliationError, reconcile_subscription,
    reconciled_allowance, require_supported_entitlement,
)
from guardoath.tenant_record import TenantRecord


class SubscriptionConflict(Exception):
    pass


class SubscriptionUnavailable(Exception):
    pass


class AdminSubscriptionReconciliation:
    def __init__(self, ddb, tenant_table, audit_table, *, signing_key,
                 resolve_snapshot, expected_price_id, expected_livemode,
                 plan_monthly_allowance, clock=None):
        if (not all(callable(getattr(ddb, name, None)) for name in ("get_item", "transact_write_items"))
                or not tenant_table or not audit_table or tenant_table == audit_table
                or not isinstance(signing_key, bytes) or len(signing_key) < 32
                or not callable(resolve_snapshot)
                or type(plan_monthly_allowance) is not int
                or not 1 <= plan_monthly_allowance <= 1_000_000_000):
            raise ValueError("Independent audited subscription reconciliation required")
        # Reuse the same plan/environment contract as the owner read projection.
        AdminSubscriptionReporter(lambda *_: None, expected_price_id=expected_price_id,
                                  expected_livemode=expected_livemode)
        self.ddb, self.tenant_table, self.audit_table = ddb, tenant_table, audit_table
        self.key, self.resolve_snapshot = signing_key, resolve_snapshot
        self.price, self.livemode, self.plan = expected_price_id, expected_livemode, plan_monthly_allowance
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _prior(self, event_id, fingerprint):
        reply = self.ddb.get_item(TableName=self.audit_table,
            Key={"event_id": {"S": event_id}}, ConsistentRead=True)
        if not isinstance(reply, dict):
            raise SubscriptionUnavailable("Reconciliation history unavailable")
        event = reply.get("Item")
        if event is None:
            return None
        if (not verify_audit(event, self.key)
                or event.get("operation") != {"S": "billing.reconcile"}):
            raise SubscriptionUnavailable("Reconciliation receipt unavailable")
        if event.get("request_fingerprint") != {"S": fingerprint}:
            raise SubscriptionConflict("Idempotency reference already used")
        # Validate the signed receipt's full semantics, not only its MAC.
        receipt = AdminAuditReader(self.ddb, self.audit_table, verify_key=self.key).read(event_id)
        if receipt is None:
            raise SubscriptionUnavailable("Reconciliation receipt unavailable")
        change = receipt["change"]
        return {"tenant_id": receipt["tenant_id"], "revision": receipt["new_revision"],
                "subscription_status": change["after"], "provider_status": change["provider_status"],
                "monthly_allowance": change["allowance_after"], "overage": "block",
                "plan_monthly_allowance": change["plan_allowance"],
                "admin_monthly_allowance_cap": change["operator_cap"],
                "audit_event_id": event_id, "replayed": True}

    def apply(self, *, tenant_id, expected_revision, confirmation, reason, actor, idempotency_key):
        if (not isinstance(tenant_id, str) or not TENANT.fullmatch(tenant_id)
                or type(expected_revision) is not int or not 1 <= expected_revision < (1 << 53) - 1
                or confirmation != "RECONCILE " + tenant_id
                or not isinstance(reason, str) or not REASON.fullmatch(reason)
                or not isinstance(actor, str) or not SUBJECT.fullmatch(actor)
                or not isinstance(idempotency_key, str) or not IDEMPOTENCY.fullmatch(idempotency_key)):
            raise ValueError("Invalid or unconfirmed subscription reconciliation")
        event_id = hmac.new(self.key, ("billing-reconcile\0" + actor + "\0" + tenant_id +
            "\0" + idempotency_key).encode(), hashlib.sha256).hexdigest()
        fingerprint = hmac.new(self.key, b"billing-request-v1\0" + json.dumps(
            [actor, tenant_id, expected_revision, confirmation, reason],
            ensure_ascii=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
        try:
            previous = self._prior(event_id, fingerprint)
            if previous is not None:
                return previous
            from guardoath.state_contracts import new_subscription_state_enabled
            if not new_subscription_state_enabled():
                raise SubscriptionUnavailable("New reconciliation requires an accepted compatibility release")
            reply = self.ddb.get_item(TableName=self.tenant_table,
                Key={"tenant_id": {"S": tenant_id}}, ConsistentRead=True)
            item = reply.get("Item") if isinstance(reply, dict) else None
            if item is None:
                raise SubscriptionConflict("Tenant missing or revision changed")
            if item.get("revision") != {"N": str(expected_revision)}:
                raise SubscriptionConflict("Tenant missing or revision changed")
            original = item["record"]["S"]
            if not isinstance(original, str) or len(original.encode()) > 350000:
                raise SubscriptionUnavailable("Saved tenant unavailable")
            document = _strict_record_json(original)
            if (not isinstance(document, dict) or document.get("tenant_id") != tenant_id
                    or document.get("demo") is not False or document.get("billing_mode") != "stripe"
                    or document.get("status") not in ("active", "disabled")
                    or type(document.get("monthly_allowance")) is not int
                    or not 0 <= document["monthly_allowance"] <= 1_000_000_000
                    or document.get("overage") not in ("block", "allow_and_record")
                    or document.get("subscription_status") not in ("inactive", "active", "past_due", "canceled")
                    or "usage" in document or "revision" in document):
                raise SubscriptionConflict("Saved Stripe account not eligible")
            current = TenantRecord.model_validate({**document, "revision": expected_revision})
            # Validate the saved customer before any external provider request.
            StripeBinding(tenant_id, current.payer_id, current.stripe_subscription_id or "sub_unbound")
            binding, snapshot = self.resolve_snapshot(current)
            if (not isinstance(binding, StripeBinding) or binding.tenant_id != tenant_id
                    or binding.customer_id != current.payer_id
                    or current.stripe_subscription_id not in (None, binding.subscription_id)):
                raise SubscriptionUnavailable("Provider binding unavailable")
            reporter = AdminSubscriptionReporter(lambda *_: snapshot,
                expected_price_id=self.price, expected_livemode=self.livemode)
            verified = reporter.read(current.model_copy(update={"stripe_subscription_id": binding.subscription_id}))
            if verified.get("verified") is not True:
                raise SubscriptionUnavailable("Provider state unavailable")
            decision = reconcile_subscription(binding, snapshot)
            # Paused or unknown collection cannot be used to create a new active
            # entitlement. Provider status is still available in the read UI.
            try:
                require_supported_entitlement(snapshot, active=decision.active)
            except SubscriptionReconciliationError as exc:
                raise SubscriptionConflict("Supported payment collection and quantity must be verified") from exc
            quota = reconciled_allowance(current, active=decision.active, plan_monthly_allowance=self.plan)
            now = self.clock()
            if not isinstance(now, datetime) or now.tzinfo is None:
                raise SubscriptionUnavailable("Reconciliation time unavailable")
            utc = now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            updated = {**document, **quota, "subscription_status": decision.status,
                       "stripe_subscription_id": binding.subscription_id, "updated_at": utc}
            if updated["admin_monthly_allowance_cap"] is None:
                updated.pop("admin_monthly_allowance_cap")
            TenantRecord.model_validate({**updated, "revision": expected_revision + 1})
            body = json.dumps(updated, sort_keys=True, separators=(",", ":"))
            if len(body.encode()) > 350000:
                raise SubscriptionUnavailable("Saved tenant exceeds safe size")
            event = {"event_id": {"S": event_id}, "tenant_id": {"S": tenant_id},
                "actor_sub": {"S": actor}, "operation": {"S": "billing.reconcile"},
                "reason": {"S": reason}, "when_utc": {"S": utc},
                "request_fingerprint": {"S": fingerprint},
                "prior_revision": {"N": str(expected_revision)}, "new_revision": {"N": str(expected_revision + 1)},
                "old_subscription_status": {"S": current.subscription_status},
                "new_subscription_status": {"S": decision.status}, "provider_status": {"S": verified["status"]},
                "old_limit": {"N": str(current.monthly_allowance)},
                "new_limit": {"N": str(quota["monthly_allowance"])}, "plan_limit": {"N": str(self.plan)}}
            if quota["admin_monthly_allowance_cap"] is not None:
                event["cap_limit"] = {"N": str(quota["admin_monthly_allowance_cap"])}
            self.ddb.transact_write_items(TransactItems=[
                {"Update": {"TableName": self.tenant_table, "Key": {"tenant_id": {"S": tenant_id}},
                    "UpdateExpression": "SET #record = :record, #revision = :next",
                    "ConditionExpression": "attribute_exists(tenant_id) AND #revision = :prior AND #record = :original",
                    "ExpressionAttributeNames": {"#record": "record", "#revision": "revision"},
                    "ExpressionAttributeValues": {":record": {"S": body}, ":original": {"S": original},
                        ":prior": {"N": str(expected_revision)}, ":next": {"N": str(expected_revision + 1)}}}},
                {"Put": {"TableName": self.audit_table, "Item": seal_audit(event, self.key),
                         "ConditionExpression": "attribute_not_exists(event_id)"}}])
        except (SubscriptionConflict, SubscriptionUnavailable):
            raise
        except Exception as exc:
            # A lost transaction response is not permission to send another
            # write. A retry with this exact key can inspect its signed result.
            raise SubscriptionUnavailable("Audited subscription reconciliation unavailable") from exc
        return {"tenant_id": tenant_id, "revision": expected_revision + 1,
                "subscription_status": decision.status, "provider_status": verified["status"],
                "monthly_allowance": quota["monthly_allowance"], "overage": "block",
                "plan_monthly_allowance": self.plan,
                "admin_monthly_allowance_cap": quota["admin_monthly_allowance_cap"],
                "audit_event_id": event_id, "replayed": False}
