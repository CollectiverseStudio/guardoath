"""Tenant record storage. The in-memory store is for local use and tests; it follows the same
rules as the DynamoDB store: one record per tenant, check changes conditional on the record's
revision, and usage counted on the record itself."""
from datetime import datetime, timezone
from threading import Lock

from guardoath.tenant_record import EDITABLE_FIELDS, TenantRecord, usage_month


class TenantNotFound(LookupError):
    pass


class TenantConflict(RuntimeError):
    """The record changed since it was read; read it again and reapply the change."""


class UsageLimitReached(RuntimeError):
    """The tenant's monthly allowance is used up and its overage setting is block."""


class MemoryTenantStore:
    def __init__(self, records: tuple[TenantRecord, ...] = ()):
        self._lock = Lock()
        self._records: dict[str, TenantRecord] = {}
        for record in records:
            self.create(record)

    def get(self, tenant_id: str) -> TenantRecord | None:
        with self._lock:
            return self._records.get(tenant_id)

    def tenant_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._records))

    def create(self, record: TenantRecord) -> TenantRecord:
        with self._lock:
            if record.tenant_id in self._records:
                raise TenantConflict("Tenant already exists")
            stored = record.model_copy(update={"revision": 1, "updated_at": _now()})
            self._records[record.tenant_id] = stored
            return stored

    def update_checks(self, tenant_id: str, changes: dict, expected_revision: int) -> TenantRecord:
        """Change check fields and check settings only. Usage, keys and account fields are untouched."""
        unknown = set(changes) - EDITABLE_FIELDS
        if unknown:
            raise ValueError("Only check fields and check settings can be changed: " + ", ".join(sorted(unknown)))
        with self._lock:
            current = self._records.get(tenant_id)
            if current is None:
                raise TenantNotFound(tenant_id)
            if current.revision != expected_revision:
                raise TenantConflict("The tenant record changed; reload it and try again")
            updated = TenantRecord.model_validate({**current.model_dump(), **changes,
                                                   "revision": current.revision + 1, "updated_at": _now()})
            self._records[tenant_id] = updated
            return updated

    def set_key_hashes(self, tenant_id: str, key_hashes: tuple[str, ...],
                       expected_revision: int | None = None) -> TenantRecord:
        """Replace the key hashes. With expected_revision, refused (TenantConflict) if the record changed."""
        with self._lock:
            current = self._records.get(tenant_id)
            if current is None:
                raise TenantNotFound(tenant_id)
            if expected_revision is not None and current.revision != expected_revision:
                raise TenantConflict("The tenant record changed; reload it and try again")
            updated = TenantRecord.model_validate({**current.model_dump(), "key_hashes": tuple(key_hashes),
                                                   "revision": current.revision + 1, "updated_at": _now()})
            self._records[tenant_id] = updated
            return updated

    def bind_stripe_customer(self, tenant_id: str, customer_id: str,
                             expected_revision: int) -> TenantRecord:
        """Bind a server-provisioned Stripe customer once, with revision guard."""
        from guardoath.billing import BillingMode
        if not isinstance(customer_id, str) or not customer_id.startswith("cus_") or not customer_id[4:].replace("_", "").isalnum():
            raise ValueError("Invalid Stripe customer ID")
        with self._lock:
            current = self._records.get(tenant_id)
            if current is None:
                raise TenantNotFound(tenant_id)
            if current.revision != expected_revision:
                raise TenantConflict("The tenant record changed; reload it and try again")
            if current.demo or current.payer_id or current.billing_mode != BillingMode.INTERNAL or current.subscription_status != "inactive":
                raise ValueError("Stripe customer binding cannot replace an existing billing account")
            updated = TenantRecord.model_validate({**current.model_dump(),
                "billing_mode": BillingMode.STRIPE, "payer_id": customer_id,
                "revision": current.revision + 1, "updated_at": _now()})
            self._records[tenant_id] = updated
            return updated

    def apply_verified_subscription(self, tenant_id: str, *, customer_id: str,
                                    subscription_id: str, provider_subscription: dict,
                                    expected_revision: int,
                                    plan_monthly_allowance: int | None = None) -> TenantRecord:
        """Atomically apply an independently fetched Stripe subscription snapshot.

        The caller must obtain provider_subscription through the server-side Stripe
        client, never from a webhook body or customer input. A binding is required
        on the existing tenant; this method cannot establish one.
        """
        from guardoath.billing import BillingMode
        from guardoath.subscription_reconciliation import StripeBinding, reconcile_subscription
        with self._lock:
            current = self._records.get(tenant_id)
            if current is None:
                raise TenantNotFound(tenant_id)
            if current.revision != expected_revision:
                raise TenantConflict("The tenant record changed; reload it and try again")
            if current.demo or current.billing_mode != BillingMode.STRIPE or current.payer_id != customer_id:
                raise ValueError("Tenant has no matching Stripe billing account")
            decision = reconcile_subscription(StripeBinding(tenant_id, customer_id, subscription_id),
                                              provider_subscription)
            if current.stripe_subscription_id not in (None, subscription_id):
                raise ValueError("Subscription binding cannot be replaced")
            if (plan_monthly_allowance is not None and
                    (type(plan_monthly_allowance) is not int or
                     not 1 <= plan_monthly_allowance <= 1_000_000_000)):
                raise ValueError("Invalid server-configured plan allowance")
            # Cancellation/past-due removes paid quota in the same atomic record
            # write as status; a stale allowance cannot survive revocation.
            allowance = (plan_monthly_allowance if decision.active and
                         plan_monthly_allowance is not None else
                         current.monthly_allowance if decision.active else 0)
            updated = TenantRecord.model_validate({**current.model_dump(),
                "subscription_status": decision.status,
                "stripe_subscription_id": subscription_id,
                "monthly_allowance": allowance,
                "overage": "block" if not decision.active or
                           plan_monthly_allowance is not None else current.overage,
                "revision": current.revision + 1, "updated_at": _now()})
            self._records[tenant_id] = updated
            return updated

    def count_check(self, tenant_id: str, month: str | None = None) -> tuple[int, bool]:
        """Count one check for the month. Returns (used, over_allowance).

        A tenant whose overage setting is block cannot go past its allowance: the check is refused
        before it runs. Other tenants are counted past it and the extra is recorded for billing."""
        month = month or usage_month()
        with self._lock:
            current = self._records.get(tenant_id)
            if current is None:
                raise TenantNotFound(tenant_id)
            used = current.used(month)
            if current.overage == "block" and used >= current.monthly_allowance:
                raise UsageLimitReached("Monthly check limit reached")
            usage = {**current.usage, month: used + 1}
            self._records[tenant_id] = current.model_copy(update={"usage": usage})
            return used + 1, used + 1 > current.monthly_allowance

    def release_check(self, tenant_id: str, month: str) -> None:
        """Give back a counted check whose request did not produce a result."""
        with self._lock:
            current = self._records.get(tenant_id)
            if current is None or current.used(month) == 0:
                return
            usage = {**current.usage, month: current.used(month) - 1}
            self._records[tenant_id] = current.model_copy(update={"usage": usage})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
