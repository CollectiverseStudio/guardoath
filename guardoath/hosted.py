"""Hosted mode: API keys, checks and usage come from tenant records (one record per tenant).

Turned on with GUARDOATH_TENANT_STORE. The local single-key mode (GUARDOATH_API_KEY) keeps working
alongside it: keys that start with "gok_" are tenant keys, any other key is checked against the local
credentials.

Every tenant can use its record's own checks (profile "tenant") and the example profiles, by default
those in policies/playground.json (GUARDOATH_TENANT_PROFILE_FILE chooses another file). Demo users do not
get profiles that call a paid outside provider. A check request that names no profile uses the record's
default_profile.
"""
import os
from pathlib import Path
import re
from threading import Lock
from time import monotonic
from typing import Any

from pydantic import Field, ValidationError

from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import Contract, Policy
from guardoath.tenant_record import (EDITABLE_FIELDS, EXTERNAL_CHECK_IDS, MIN_DEMO_KEY_SECRET, PROFILE_PATTERN,
                                     TENANT_POLICY_VERSION, TenantRecord, demo_defaults, demo_key, demo_records,
                                     key_matches, tenant_id_from_key)
from guardoath.tenant_store import MemoryTenantStore, TenantNotFound

TENANT_STORES = ("memory", "dynamodb")
# How long the demo user list is kept before the store is read again (dynamodb only).
DEMO_LIST_SECONDS = 60.0
DEFAULT_PROFILE_FILE = Path(__file__).resolve().parents[1] / "policies" / "playground.json"


def calls_outside_provider(policy: Policy) -> bool:
    return policy.external_checks != "disabled" or any(rule.checker_id in EXTERNAL_CHECK_IDS for rule in policy.rules)


def load_example_profiles(path: str | Path | None = None) -> tuple[Policy, ...]:
    """The example profiles every tenant may use. A missing file stops startup instead of silently
    offering no profiles."""
    file = Path(path) if path else DEFAULT_PROFILE_FILE
    if not file.is_file():
        raise ValueError("Example profile file not found; set GUARDOATH_TENANT_PROFILE_FILE")
    return tuple(load_policies(file))


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
    def __init__(self, store, checkers, demo_key_secret: str | None = None, profiles: tuple[Policy, ...] = (),
                 demo_list_seconds: float = 0.0):
        if demo_key_secret is not None and len(demo_key_secret) < MIN_DEMO_KEY_SECRET:
            raise ValueError(f"GUARDOATH_DEMO_KEY_SECRET must be at least {MIN_DEMO_KEY_SECRET} characters")
        versions = [profile.version for profile in profiles]
        if len(set(versions)) != len(versions):
            raise ValueError("Example profile versions must be distinct")
        if TENANT_POLICY_VERSION in versions:
            raise ValueError(f"No example profile may be called {TENANT_POLICY_VERSION}")
        if any(not re.fullmatch(PROFILE_PATTERN, version) for version in versions):
            raise ValueError("Example profile versions must be lower-case letters, digits and hyphens")
        self.store = store
        self._checkers = dict(checkers)
        self._demo_key_secret = demo_key_secret
        self._profiles = tuple(profiles)
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
        profiles = load_example_profiles(os.getenv("GUARDOATH_TENANT_PROFILE_FILE"))
        if store_name == "memory":
            # The memory store starts with the demo users only; it is for local use and tests.
            return cls(MemoryTenantStore(demo_records(key_secret=secret) if secret else ()), checkers, secret, profiles)
        from guardoath.dynamodb_store import DynamoDBTenantStore, ensure_demo_records
        store = DynamoDBTenantStore.from_environment()
        if secret:
            # Missing demo users are created; existing ones keep their checks and usage.
            ensure_demo_records(store, demo_records(key_secret=secret))
        return cls(store, checkers, secret, profiles, demo_list_seconds=DEMO_LIST_SECONDS)

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

    def profiles_for(self, record: TenantRecord) -> tuple[Policy, ...]:
        """The example profiles this tenant may use, as the tenant's own. Demo users get none that call a
        paid outside provider."""
        return tuple(profile.model_copy(update={"application_id": record.tenant_id}) for profile in self._profiles
                     if not (record.demo and calls_outside_provider(profile)))

    def profile_versions(self, record: TenantRecord) -> tuple[str, ...]:
        """Every profile the tenant may name: its record's own checks first, then the example profiles."""
        return (TENANT_POLICY_VERSION, *(profile.version for profile in self.profiles_for(record)))

    def engine_for(self, record: TenantRecord) -> Engine:
        """An engine with the record's own checks and the tenant's example profiles, rebuilt whenever
        the record changes."""
        with self._lock:
            cached = self._engines.get(record.tenant_id)
            if cached is not None and cached[0] == record.revision:
                return cached[1]
        engine = Engine([record.to_policy(), *self.profiles_for(record)], self._checkers)
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
        if set(changes) - EDITABLE_FIELDS:
            raise ChangeRejected("Only check fields and check settings can be changed")
        current = self.store.get(tenant_id)
        if current is None:
            raise TenantNotFound(tenant_id)
        try:
            candidate = TenantRecord.model_validate({**current.model_dump(), **changes})
        except ValidationError as error:
            raise ChangeRejected(rejection_message(error)) from None
        if candidate.default_profile not in self.profile_versions(candidate):
            raise ChangeRejected("default_profile: choose one of the profiles available to this tenant")
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
