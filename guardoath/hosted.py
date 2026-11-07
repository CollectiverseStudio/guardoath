"""Hosted mode: API keys, checks and usage come from tenant records (one record per tenant).

Turned on with GUARDOATH_TENANT_STORE. The local single-key mode (GUARDOATH_API_KEY) keeps working
alongside it: keys that start with "gok_" are tenant keys, any other key is checked against the local
credentials.

A tenant key runs the checks on its tenant's record and nothing else: there are no example profiles for
tenants (removed 2026-11-02). A check request may leave policy_version out or send "tenant"; any other
value is refused (404) and not counted.
"""
import os
from threading import Lock
from time import monotonic
from typing import Any

from pydantic import Field, ValidationError

from guardoath.engine import Engine
from guardoath.models import Contract
from guardoath.tenant_record import (EDITABLE_FIELDS, LEGACY_FIELDS, MAX_RECORD_BYTES, MIN_DEMO_KEY_SECRET, RETIRED_CHECK_FIELDS,
                                     TENANT_POLICY_VERSION, TenantRecord,
                                     demo_defaults, demo_key, demo_records, key_matches, record_bytes, tenant_id_from_key)
from guardoath.tenant_store import MemoryTenantStore, TenantNotFound

TENANT_STORES = ("memory", "dynamodb")
# How long the demo user list is kept before the store is read again (dynamodb only).
DEMO_LIST_SECONDS = 60.0
# Retired setting: example profiles were removed for tenants on 2026-11-02.
RETIRED_PROFILE_SETTING = "GUARDOATH_TENANT_PROFILE_FILE"


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
    def __init__(self, store, checkers, demo_key_secret: str | None = None, demo_list_seconds: float = 0.0):
        if demo_key_secret is not None and len(demo_key_secret) < MIN_DEMO_KEY_SECRET:
            raise ValueError(f"GUARDOATH_DEMO_KEY_SECRET must be at least {MIN_DEMO_KEY_SECRET} characters")
        self.store = store
        self._checkers = dict(checkers)
        self._demo_key_secret = demo_key_secret
        self._demo_list_seconds = demo_list_seconds
        self._demo_list: tuple[float, list[dict]] | None = None
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
        if secret is not None and len(secret) < MIN_DEMO_KEY_SECRET:
            raise ValueError(f"GUARDOATH_DEMO_KEY_SECRET must be at least {MIN_DEMO_KEY_SECRET} characters")
        if os.getenv(RETIRED_PROFILE_SETTING) is not None:
            # Fail at startup rather than run without the profiles the setting asks for.
            raise ValueError(RETIRED_PROFILE_SETTING + " is no longer used: tenants run only their record's checks")
        if store_name == "memory":
            # The memory store starts with the demo users only; it is for local use and tests.
            return cls(MemoryTenantStore(demo_records(key_secret=secret) if secret else ()), checkers, secret)
        from guardoath.dynamodb_store import DynamoDBTenantStore, ensure_demo_records
        store = DynamoDBTenantStore.from_environment()
        if secret:
            # Missing demo users are created; existing ones keep their checks and usage.
            ensure_demo_records(store, demo_records(key_secret=secret))
        return cls(store, checkers, secret, demo_list_seconds=DEMO_LIST_SECONDS)

    @property
    def checkers(self) -> dict:
        """The detectors this service runs, by control id (a copy)."""
        return dict(self._checkers)

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

    def policy_versions(self, record: TenantRecord) -> tuple[str, ...]:
        """The one profile a tenant can run: its record's own checks."""
        return (TENANT_POLICY_VERSION,)

    def engine_for(self, record: TenantRecord) -> Engine:
        """An engine with the record's own checks, rebuilt whenever the record changes."""
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
        cached = self._demo_list
        if cached is not None and monotonic() < cached[0]:
            return list(cached[1])
        users = []
        for tenant_id in self.store.tenant_ids():
            record = self.store.get(tenant_id)
            if record is not None and record.demo and record.status == "active":
                users.append({"tenant_id": record.tenant_id, "display_name": record.display_name})
        users.sort(key=lambda user: (len(user["tenant_id"]), user["tenant_id"]))
        if self._demo_list_seconds > 0:
            self._demo_list = (monotonic() + self._demo_list_seconds, users)
        return list(users)

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
        if set(changes) & RETIRED_CHECK_FIELDS:
            raise ChangeRejected("content_safety and azure_prompt_shields were removed (2026-11-05): GuardOath runs every "
                                 "check itself. Use harmful_content and prompt_attack")
        if set(changes) & LEGACY_FIELDS:
            raise ChangeRejected("check_<id> and rule_actions were renamed (2026-11-04): send input_check_<id> and "
                                 "output_check_<id>, and input_rule_actions and output_rule_actions")
        if set(changes) - EDITABLE_FIELDS:
            raise ChangeRejected("Only check fields and check settings can be changed")
        current = self.store.get(tenant_id)
        if current is None:
            raise TenantNotFound(tenant_id)
        # The general checks endpoint accepts tenant-authored identity equality and
        # uniqueness only. Host/administrator provisioned identity reference sets
        # must never be overwritten by a replacement array from the dashboard or
        # from a leaked/low-privilege API key. No implicit merge of private values.
        if "advanced_identity" in changes:
            protected_kinds = {"quarantine_exclusion", "authorized_scope_subset"}
            if any(rule.kind in protected_kinds for rule in current.advanced_identity):
                raise ChangeRejected("Identity definitions with protected reference sets must be managed "
                                     "through an authorized reference-management workflow")
            supplied = changes["advanced_identity"]
            if not isinstance(supplied, (list, tuple)) or any(
                    (getattr(rule, "kind", None) if not isinstance(rule, dict)
                     else rule.get("kind")) not in {"order_id_match", "serial_unique"}
                    for rule in supplied):
                raise ChangeRejected("The checks endpoint accepts only non-reference identity kinds")
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
        size = record_bytes(candidate)
        if size > MAX_RECORD_BYTES:
            raise ChangeRejected(f"This change would make the record {size:,} bytes. A record is stored as one database "
                                 f"item and its settings can be at most {MAX_RECORD_BYTES:,} bytes; remove some "
                                 "comparisons, sections or other settings.")
        return self.store.update_checks(tenant_id, changes, expected_revision)

    def reset_demo(self, tenant_id: str) -> TenantRecord:
        """Set a demo user's check fields and settings back to the demo defaults."""
        current = self.store.get(tenant_id)
        if current is None:
            raise TenantNotFound(tenant_id)
        if not current.demo:
            raise NotADemoUser("Only demo users can be reset to the demo defaults")
        return self.change_checks(tenant_id, demo_defaults(), current.revision)
