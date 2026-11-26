"""Reviewed restoration of an explicit saved local request cap.

The configured plan is independently re-read from the already-bound Stripe
subscription. Only the local cap and allowance change. The exact tenant document
and revision are conditional, and the immutable signed receipt commits with the
change. Historical replay never obtains fresh write authority.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import hmac
import json
import math
import re
import secrets
from threading import Lock
import time
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from guardoath.admin_actions import TENANT
from guardoath.admin_audit import SUBJECT
from guardoath.admin_cancellation_provider import canonical, digest, provider_id
from guardoath.admin_cap_restore_provider import (
    MAX_TIME, CapRestorationEntitlementError, validate_restoration_entitlement,
)
from guardoath.admin_cap_restore_state import (
    CONTRACT, OPERATION, CapRestoreStateConflict,
    DynamoCapRestoreState,
)
from guardoath.admin_directory import _strict_record_json
from guardoath.dynamodb_store import from_item
from guardoath.state_contracts import new_cap_restoration_state_enabled
from guardoath.stripe_provider import _strict_provider_json
from guardoath.subscription_reconciliation import StripeBinding
from guardoath.tenant_record import MAX_RECORD_BYTES, TenantRecord

MAX_REVISION = (1 << 53) - 1


class CapRestoreConflict(Exception):
    """The saved cap, provider entitlement, review or request no longer matches."""


class CapRestoreUnavailable(Exception):
    """An authoritative result is unavailable; retain the original reference."""


def _utc(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AdminRequestCapRestoration:
    PREVIEW_SECONDS = 180
    EXECUTION_BUDGET_SECONDS = 8.0

    def __init__(self, *, state: DynamoCapRestoreState, tenant_table: str,
                 preview_key: bytes, fetch_snapshot=None, expected_price_id=None,
                 expected_livemode=None, plan_monthly_allowance=None, clock=None):
        configured = any(value is not None for value in (
            fetch_snapshot, expected_price_id, expected_livemode, plan_monthly_allowance))
        if (not isinstance(state, DynamoCapRestoreState)
                or not isinstance(tenant_table, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", tenant_table)
                or tenant_table == state.table_name
                or not isinstance(preview_key, bytes) or len(preview_key) < 32
                or (clock is not None and not callable(clock))
                or (configured and (not callable(fetch_snapshot)
                    or not provider_id(expected_price_id, "price")
                    or type(expected_livemode) is not bool
                    or type(plan_monthly_allowance) is not int
                    or not 1 <= plan_monthly_allowance <= 1_000_000_000))):
            raise ValueError("Independent signed cap restoration configuration required")
        self.state, self.tenant_table = state, tenant_table
        self.provider_configured = configured
        self._fetch_snapshot = fetch_snapshot
        self.expected_price_id, self.expected_livemode = expected_price_id, expected_livemode
        self.plan_monthly_allowance = plan_monthly_allowance
        self._clock = time.time if clock is None else clock
        self._cipher = AESGCM(hmac.new(preview_key, b"guardoath-cap-restore-preview-v1",
                                      hashlib.sha256).digest())
        self._target_key = hmac.new(preview_key, b"guardoath-cap-restore-target-v1",
                                    hashlib.sha256).digest()
        self._budget, self._lock = OrderedDict(), Lock()

    @staticmethod
    def _identity(actor: str, tenant_id: str, expected_revision: int | None = None) -> None:
        if (not isinstance(actor, str) or not SUBJECT.fullmatch(actor)
                or not isinstance(tenant_id, str) or not TENANT.fullmatch(tenant_id)
                or (expected_revision is not None and
                    (type(expected_revision) is not int or not 1 <= expected_revision < MAX_REVISION))):
            raise ValueError("Invalid cap restoration context")

    def _now(self) -> int:
        try:
            value = self._clock()
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or not 946684800 <= value < 4102444800 - self.PREVIEW_SECONDS):
                raise ValueError()
            return int(value)
        except Exception:
            raise CapRestoreUnavailable() from None

    def _reserve_read(self, actor: str, now: int) -> None:
        with self._lock:
            for previous_actor, (started, _) in list(self._budget.items()):
                if now - started >= 60:
                    self._budget.pop(previous_actor)
            started, count = self._budget.get(actor, (now, 0))
            if now < started or count >= 20 or (actor not in self._budget and len(self._budget) >= 128):
                raise CapRestoreUnavailable() from None
            self._budget[actor] = (started, count + 1)

    @staticmethod
    def _remaining(deadline: float, *, reserve: float = 0) -> float:
        value = deadline - time.monotonic() - reserve
        if not math.isfinite(value) or value <= 0:
            raise CapRestoreUnavailable() from None
        return value

    def _tenant(self, tenant_id: str, revision: int):
        try:
            response = self.state.client.get_item(TableName=self.tenant_table,
                Key={"tenant_id": {"S": tenant_id}}, ConsistentRead=True)
            if not isinstance(response, dict):
                raise CapRestoreUnavailable()
            item = response.get("Item")
            if (not isinstance(item, dict) or item.get("tenant_id") != {"S": tenant_id}
                    or item.get("revision") != {"N": str(revision)}):
                raise CapRestoreConflict()
            if not isinstance(item.get("record"), dict) or set(item["record"]) != {"S"}:
                raise CapRestoreUnavailable()
            document = item["record"]["S"]
            if not isinstance(document, str) or len(document.encode("utf-8")) > MAX_RECORD_BYTES:
                raise CapRestoreUnavailable()
            fields = _strict_record_json(document)
            if (not isinstance(fields, dict) or {"usage", "revision"} & set(fields)
                    or fields.get("tenant_id") != tenant_id or fields.get("demo") is not False
                    or fields.get("billing_mode") != "stripe"
                    or fields.get("status") not in ("active", "disabled")
                    or fields.get("subscription_status") != "active" or fields.get("overage") != "block"
                    or type(fields.get("admin_monthly_allowance_cap")) is not int
                    or type(fields.get("monthly_allowance")) is not int
                    or type(fields.get("stripe_plan_monthly_allowance")) is not int
                    or fields["stripe_plan_monthly_allowance"] != self.plan_monthly_allowance
                    or not 0 <= fields["admin_monthly_allowance_cap"] < self.plan_monthly_allowance
                    or fields["monthly_allowance"] != fields["admin_monthly_allowance_cap"]):
                raise CapRestoreConflict()
            record = from_item(item)
            binding = StripeBinding(tenant_id, record.payer_id, record.stripe_subscription_id)
            return record, binding, document, fields
        except CapRestoreConflict:
            raise
        except Exception:
            raise CapRestoreUnavailable() from None

    def _fresh(self, binding: StripeBinding, *, started: int, deadline: float):
        try:
            timeout = min(5.0, self._remaining(deadline, reserve=2.0))
            snapshot = self._fetch_snapshot(binding, timeout=timeout)
            observed = self._now()
            if observed < started:
                raise CapRestoreUnavailable()
            self._remaining(deadline, reserve=1.0)
            proof = validate_restoration_entitlement(snapshot, binding,
                expected_price_id=self.expected_price_id, expected_livemode=self.expected_livemode,
                now=observed)
            return proof, observed
        except CapRestorationEntitlementError:
            raise CapRestoreConflict() from None
        except Exception:
            raise CapRestoreUnavailable() from None

    @staticmethod
    def _aad(actor: str, tenant_id: str) -> bytes:
        return canonical([CONTRACT, actor, tenant_id]).encode("ascii")

    def _target(self, tenant_id: str, proof: dict) -> str:
        body = canonical([tenant_id, proof["customer_id"], proof["subscription_id"],
                          proof["price_id"], proof["livemode"]]).encode("ascii")
        return hmac.new(self._target_key, body, hashlib.sha256).hexdigest()

    def _configuration_hash(self) -> str:
        return digest([CONTRACT, self.expected_price_id, self.expected_livemode, self.plan_monthly_allowance])

    @staticmethod
    def _confirmation(tenant_id: str, plan: int) -> str:
        return f"RESTORE {tenant_id} TO {plan} REQUESTS PER MONTH"

    def _encode(self, value: dict, actor: str, tenant_id: str) -> str:
        nonce = secrets.token_bytes(12)
        sealed = self._cipher.encrypt(nonce, canonical(value).encode("ascii"), self._aad(actor, tenant_id))
        return "cr1." + base64.urlsafe_b64encode(nonce + sealed).decode("ascii").rstrip("=")

    def _decode(self, token: str, actor: str, tenant_id: str, now: int) -> dict:
        try:
            if not isinstance(token, str) or not re.fullmatch(r"cr1\.[A-Za-z0-9_-]{40,4092}", token):
                raise ValueError()
            encoded = token[4:]
            raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != encoded:
                raise ValueError()
            value = _strict_provider_json(self._cipher.decrypt(raw[:12], raw[12:], self._aad(actor, tenant_id)))
            if (set(value) != {"version", "revision", "record_hash", "provider_hash", "target_fingerprint",
                               "configuration_hash", "plan_allowance", "issued", "expires", "period_end"}
                    or any(type(value[k]) is not int for k in
                           ("version", "revision", "plan_allowance", "issued", "expires", "period_end"))
                    or value["version"] != 1 or not 1 <= value["revision"] < MAX_REVISION
                    or not 1 <= value["plan_allowance"] <= 1_000_000_000
                    or not 946684800 <= value["issued"] <= now < value["expires"] <= 4102444800
                    or not value["issued"] < value["period_end"] <= MAX_TIME
                    or value["expires"] != min(value["issued"] + self.PREVIEW_SECONDS, value["period_end"])
                    or any(not isinstance(value[k], str) or not re.fullmatch(r"[a-f0-9]{64}", value[k])
                           for k in ("record_hash", "provider_hash", "target_fingerprint", "configuration_hash"))):
                raise ValueError()
            return value
        except Exception:
            raise CapRestoreConflict() from None

    def preview(self, *, actor: str, tenant_id: str, expected_revision: int) -> dict:
        self._identity(actor, tenant_id, expected_revision)
        if not self.provider_configured:
            raise CapRestoreUnavailable() from None
        deadline = time.monotonic() + self.EXECUTION_BUDGET_SECONDS
        now = self._now()
        self._reserve_read(actor, now)
        record, binding, document, _ = self._tenant(tenant_id, expected_revision)
        proof, observed = self._fresh(binding, started=now, deadline=deadline)
        value = {"version": 1, "revision": expected_revision,
            "record_hash": hashlib.sha256(document.encode("utf-8")).hexdigest(),
            "provider_hash": digest(proof), "target_fingerprint": self._target(tenant_id, proof),
            "configuration_hash": self._configuration_hash(), "plan_allowance": self.plan_monthly_allowance,
            "issued": observed, "expires": min(observed + self.PREVIEW_SECONDS, proof["period_end"]),
            "period_end": proof["period_end"]}
        return {"status": "reviewed", "operation": CONTRACT, "tenant_id": tenant_id,
            "expected_revision": expected_revision, "allowance_before": record.monthly_allowance,
            "operator_cap_before": record.admin_monthly_allowance_cap, "plan_allowance": self.plan_monthly_allowance,
            "allowance_after": self.plan_monthly_allowance, "operator_cap_after": None,
            "tenant_status": record.status, "subscription_status": "active", "provider_status": "active",
            "overage": "block", "livemode": proof["livemode"], "observed_at_utc": _utc(observed),
            "expires_at_utc": _utc(value["expires"]), "current_period_start_utc": _utc(proof["period_start"]),
            "current_period_end_utc": _utc(proof["period_end"]),
            "cancellation": {"cancel_at_period_end": proof["cancel_at_period_end"],
                             "cancel_at_utc": _utc(proof["cancel_at"]) if proof["cancel_at"] is not None else None},
            "target_fingerprint": value["target_fingerprint"], "preview_reference": self._encode(value, actor, tenant_id),
            "confirmation": self._confirmation(tenant_id, self.plan_monthly_allowance),
            "restore_available": new_cap_restoration_state_enabled(), "tenant_mutated": False}

    @staticmethod
    def _projection(receipt: dict, *, actor: str, tenant_id: str) -> dict:
        if receipt["actor_sub"] != actor or receipt["tenant_id"] != tenant_id:
            raise CapRestoreConflict() from None
        return {"status": "applied", "operation": CONTRACT, "tenant_id": tenant_id,
            "action_id": receipt["event_id"], "audit_event_id": receipt["event_id"],
            "prior_revision": receipt["prior_revision"], "revision": receipt["new_revision"],
            "allowance_before": receipt["old_limit"], "operator_cap_before": receipt["cap_limit"],
            "plan_allowance": receipt["plan_limit"], "allowance_after": receipt["new_limit"],
            "operator_cap_after": None, "tenant_status": receipt["new_status"], "subscription_status": "active",
            "overage": "block", "reason": receipt["reason"], "recorded_at_utc": receipt["when_utc"],
            "target_fingerprint": receipt["target_fingerprint"], "integrity_status": "signed_verified",
            "replayed": receipt["replayed"], "historical": True}

    def receipt(self, *, actor: str, tenant_id: str, idempotency_key: str) -> dict:
        self._identity(actor, tenant_id)
        action_id = self.state.action_id(actor, idempotency_key)
        try:
            previous = self.state.history(action_id)
            if previous is None:
                return {"status": "not_found", "operation": CONTRACT, "tenant_id": tenant_id,
                        "action_id": action_id, "tenant_mutated": False}
            return self._projection(previous, actor=actor, tenant_id=tenant_id)
        except (CapRestoreConflict, CapRestoreStateConflict):
            raise CapRestoreConflict() from None
        except Exception:
            raise CapRestoreUnavailable() from None

    def apply(self, *, actor: str, tenant_id: str, expected_revision: int,
              preview_reference: str, confirmation: str, reason: str, idempotency_key: str) -> dict:
        self._identity(actor, tenant_id, expected_revision)
        action_id = self.state.action_id(actor, idempotency_key)
        request_hash = self.state.request_fingerprint(actor=actor, tenant_id=tenant_id,
            expected_revision=expected_revision, preview_reference=preview_reference,
            confirmation=confirmation, reason=reason)
        try:
            previous = self.state.history(action_id, request_fingerprint=request_hash)
            if previous is not None:
                return self._projection(previous, actor=actor, tenant_id=tenant_id)
        except (CapRestoreConflict, CapRestoreStateConflict):
            raise CapRestoreConflict() from None
        except Exception:
            raise CapRestoreUnavailable() from None
        if not self.provider_configured or not new_cap_restoration_state_enabled():
            raise CapRestoreUnavailable() from None
        deadline = time.monotonic() + self.EXECUTION_BUDGET_SECONDS
        now = self._now()
        review = self._decode(preview_reference, actor, tenant_id, now)
        if (review["revision"] != expected_revision or review["plan_allowance"] != self.plan_monthly_allowance
                or review["configuration_hash"] != self._configuration_hash()
                or confirmation != self._confirmation(tenant_id, self.plan_monthly_allowance)):
            raise CapRestoreConflict() from None
        self._reserve_read(actor, now)
        record, binding, document, fields = self._tenant(tenant_id, expected_revision)
        if hashlib.sha256(document.encode("utf-8")).hexdigest() != review["record_hash"]:
            raise CapRestoreConflict() from None
        proof, observed = self._fresh(binding, started=now, deadline=deadline)
        if (digest(proof) != review["provider_hash"]
                or self._target(tenant_id, proof) != review["target_fingerprint"]):
            raise CapRestoreConflict() from None
        committed_at = self._now()
        if not observed <= committed_at < review["expires"]:
            raise CapRestoreConflict() from None
        self._remaining(deadline, reserve=1.0)
        when = _utc(committed_at)
        updated = {**fields, "monthly_allowance": self.plan_monthly_allowance, "updated_at": when}
        updated.pop("admin_monthly_allowance_cap")
        TenantRecord.model_validate({**updated, "revision": expected_revision + 1})
        updated_document = json.dumps(updated, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(updated_document.encode("utf-8")) > MAX_RECORD_BYTES:
            raise CapRestoreConflict() from None
        audit = {"event_id": action_id, "tenant_id": tenant_id, "actor_sub": actor, "operation": OPERATION,
            "when_utc": when, "reason": reason, "prior_revision": expected_revision, "new_revision": expected_revision + 1,
            "old_status": record.status, "new_status": record.status,
            "old_subscription_status": "active", "new_subscription_status": "active", "provider_status": "active",
            "old_limit": record.monthly_allowance, "new_limit": self.plan_monthly_allowance,
            "plan_limit": self.plan_monthly_allowance, "cap_limit": record.admin_monthly_allowance_cap,
            "target_fingerprint": review["target_fingerprint"], "request_fingerprint": request_hash}
        def before_write():
            # State performs its own final history read and model validation.
            # Recheck the short review at the actual transaction boundary so a
            # delayed state read cannot outlive the reviewed billing period.
            if not committed_at <= self._now() < review["expires"]:
                raise CapRestoreConflict() from None
            self._remaining(deadline, reserve=1.0)
        try:
            saved = self.state.commit(audit, tenant_table=self.tenant_table,
                                      original_record=document, updated_record=updated_document,
                                      before_write=before_write)
            return self._projection(saved, actor=actor, tenant_id=tenant_id)
        except (CapRestoreConflict, CapRestoreStateConflict):
            raise CapRestoreConflict() from None
        except Exception:
            raise CapRestoreUnavailable() from None
