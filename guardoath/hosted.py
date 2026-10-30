"""Hosted mode: API keys, checks and usage come from tenant records (one record per tenant).

Turned on with GUARDOATH_TENANT_STORE. The local single-key mode (GUARDOATH_API_KEY) keeps working
alongside it: keys that start with "gok_" are tenant keys, any other key is checked against the local
credentials.
"""
import os
from threading import Lock

from guardoath.engine import Engine
from guardoath.tenant_record import (MIN_DEMO_KEY_SECRET, TenantRecord, demo_key, demo_records, key_matches,
                                     tenant_id_from_key)
from guardoath.tenant_store import MemoryTenantStore

TENANT_STORES = ("memory",)


class TenantService:
    def __init__(self, store, checkers, demo_key_secret: str | None = None):
        if demo_key_secret is not None and len(demo_key_secret) < MIN_DEMO_KEY_SECRET:
            raise ValueError(f"GUARDOATH_DEMO_KEY_SECRET must be at least {MIN_DEMO_KEY_SECRET} characters")
        self.store = store
        self._checkers = dict(checkers)
        self._demo_key_secret = demo_key_secret
        self._engines: dict[str, tuple[int, Engine]] = {}
        self._lock = Lock()

    @classmethod
    def from_environment(cls, checkers):
        """None when GUARDOATH_TENANT_STORE is unset."""
        store_name = os.getenv("GUARDOATH_TENANT_STORE")
        secret = os.getenv("GUARDOATH_DEMO_KEY_SECRET")
        if store_name is None:
            if secret is not None:
                raise ValueError("GUARDOATH_DEMO_KEY_SECRET requires GUARDOATH_TENANT_STORE")
            return None
        if store_name not in TENANT_STORES:
            raise ValueError("GUARDOATH_TENANT_STORE must be one of: " + ", ".join(TENANT_STORES))
        # The memory store starts with the demo users only; it is for local use and tests.
        return cls(MemoryTenantStore(demo_records(key_secret=secret) if secret else ()), checkers, secret)

    @property
    def demo_enabled(self) -> bool:
        return self._demo_key_secret is not None

    def authenticate(self, token: str) -> TenantRecord | None:
        tenant_id = tenant_id_from_key(token)
        if tenant_id is None:
            return None
        record = self.store.get(tenant_id)
        if record is None or record.status != "active" or not key_matches(record, token):
            return None
        return record

    def engine_for(self, record: TenantRecord) -> Engine:
        """An engine built from the record's check fields, rebuilt whenever the record changes."""
        with self._lock:
            cached = self._engines.get(record.tenant_id)
            if cached is not None and cached[0] == record.revision:
                return cached[1]
        engine = Engine([record.to_policy()], self._checkers)
        with self._lock:
            self._engines[record.tenant_id] = (record.revision, engine)
        return engine

    def demo_users(self) -> list[dict]:
        if not self.demo_enabled:
            return []
        users = []
        for tenant_id in self.store.tenant_ids():
            record = self.store.get(tenant_id)
            if record is not None and record.demo and record.status == "active":
                users.append({"tenant_id": record.tenant_id, "display_name": record.display_name})
        return sorted(users, key=lambda user: (len(user["tenant_id"]), user["tenant_id"]))

    def demo_key(self, tenant_id: str) -> str | None:
        """The API key of an active demo user, or None."""
        if not self.demo_enabled:
            return None
        record = self.store.get(tenant_id)
        if record is None or not record.demo or record.status != "active":
            return None
        token = demo_key(tenant_id, self._demo_key_secret)
        return token if key_matches(record, token) else None
