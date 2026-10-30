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

    def set_key_hashes(self, tenant_id: str, key_hashes: tuple[str, ...]) -> TenantRecord:
        with self._lock:
            current = self._records.get(tenant_id)
            if current is None:
                raise TenantNotFound(tenant_id)
            updated = TenantRecord.model_validate({**current.model_dump(), "key_hashes": tuple(key_hashes),
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
