"""Hosted mode: API keys, checks and usage come from tenant records (one record per tenant).

Turned on with GUARDOATH_TENANT_STORE. The local single-key mode (GUARDOATH_API_KEY) keeps working
alongside it: keys that start with "gok_" are tenant keys, any other key is checked against the local
credentials.
"""
import os
from threading import Lock
from typing import Any

from pydantic import Field, ValidationError

from guardoath.engine import Engine
from guardoath.models import Contract
from guardoath.tenant_record import (EDITABLE_FIELDS, MIN_DEMO_KEY_SECRET, TenantRecord, demo_defaults, demo_key,
                                     demo_records, key_matches, tenant_id_from_key)
from guardoath.tenant_store import MemoryTenantStore, TenantNotFound

TENANT_STORES = ("memory",)


class CheckChanges(Contract):
    """A change to a tenant's own check fields and check settings, made against a known revision."""
    revision: int = Field(ge=1, strict=True)
    changes: dict[str, Any] = Field(min_length=1, max_length=len(EDITABLE_FIELDS))


class ChangeRejected(ValueError):
    """A change that was not saved. The message is safe to return: it never contains submitted values."""


class NotADemoUser(PermissionError):
    pass


def rejection_message(error: ValidationError) -> str:
    messages = []
    for item in error.errors():
        field = item["loc"][0] if item.get("loc") and item["loc"][0] in TenantRecord.model_fields else None
        message = item["msg"].removeprefix("Value error, ")
        messages.append(f"{field}: {message}" if field else message)
    return "; ".join(dict.fromkeys(messages))[:600]


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

    def change_checks(self, tenant_id: str, changes: dict, expected_revision: int) -> TenantRecord:
        """Save a change to the record's check fields and settings. The engine is built from the result
        first, so a change the engine would reject is never stored."""
        if set(changes) - EDITABLE_FIELDS:
            raise ChangeRejected("Only check fields and check settings can be changed")
        current = self.store.get(tenant_id)
        if current is None:
            raise TenantNotFound(tenant_id)
        try:
            candidate = TenantRecord.model_validate({**current.model_dump(), **changes})
        except ValidationError as error:
            raise ChangeRejected(rejection_message(error)) from None
        try:
            Engine([candidate.to_policy()], self._checkers)
        except ValueError as error:
            raise ChangeRejected(str(error)) from None
        except Exception:
            raise ChangeRejected("The checks could not be built from these settings") from None
        return self.store.update_checks(tenant_id, changes, expected_revision)

    def reset_demo(self, tenant_id: str) -> TenantRecord:
        """Set a demo user's check fields and settings back to the demo defaults."""
        current = self.store.get(tenant_id)
        if current is None:
            raise TenantNotFound(tenant_id)
        if not current.demo:
            raise NotADemoUser("Only demo users can be reset to the demo defaults")
        return self.change_checks(tenant_id, demo_defaults(), current.revision)
