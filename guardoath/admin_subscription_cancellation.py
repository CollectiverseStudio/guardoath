"""Reviewed owner cancellation with one dispatch and durable uncertainty recovery.

Scheduling never edits a tenant, allowance, key, usage counter or customer rule.
Historical recovery reads signed evidence and never retries the Stripe POST.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re
import secrets
from threading import Lock
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from guardoath.admin_actions import IDEMPOTENCY, REASON, TENANT
from guardoath.admin_cancellation_provider import StripePeriodEndCancellation, canonical, digest, provider_id
from guardoath.admin_cancellation_state import (
    CONTRACT, MAX_REVISION, DynamoCancellationState, CancellationStateConflict,
    CancellationStateUnavailable, intent_hash,
)
from guardoath.admin_audit import SUBJECT
from guardoath.dynamodb_store import from_item
from guardoath.state_contracts import new_cancellation_state_enabled
from guardoath.subscription_reconciliation import StripeBinding
from guardoath.stripe_provider import _strict_provider_json


class CancellationConflict(Exception):
    """The review, tenant or serialized provider target no longer matches."""


class CancellationUnavailable(Exception):
    """No verified operation result is available; retain the action reference."""


class CancellationRateLimited(Exception):
    pass


def utc(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AdminSubscriptionCancellation:
    PREVIEW_SECONDS = 180

    def __init__(self, *, state: DynamoCancellationState, tenant_table: str,
                 preview_key: bytes, provider: StripePeriodEndCancellation | None = None,
                 clock=time.time):
        if (not isinstance(state, DynamoCancellationState)
                or not isinstance(tenant_table, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", tenant_table)
                or tenant_table == state.table_name
                or not isinstance(preview_key, bytes) or len(preview_key) < 32
                or (provider is not None and not isinstance(provider, StripePeriodEndCancellation))
                or not callable(clock)):
            raise ValueError("Independent owner cancellation configuration required")
        self.state, self.tenant_table, self.provider = state, tenant_table, provider
        self._clock = clock
        self._cipher = AESGCM(hmac.new(preview_key, b"guardoath-cancellation-preview-v1",
                                      hashlib.sha256).digest())
        self._budget, self._lock = OrderedDict(), Lock()

    def _now(self) -> int:
        now = self._clock()
        if (isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now)
                or not 0 < now < 253402300799):
            raise CancellationUnavailable() from None
        return int(now)

    @staticmethod
    def _identity(actor: str, tenant_id: str, expected_revision: int | None = None) -> None:
        if (not isinstance(actor, str) or not SUBJECT.fullmatch(actor)
                or not isinstance(tenant_id, str) or not TENANT.fullmatch(tenant_id)
                or (expected_revision is not None and
                    (type(expected_revision) is not int or not 1 <= expected_revision <= MAX_REVISION))):
            raise ValueError("Invalid cancellation context")

    def _reserve_read(self, actor: str, now: int) -> None:
        # Best effort per warm process, not a global or financial action lock.
        # Durable target control is the independent dispatch safety mechanism.
        with self._lock:
            for old_actor, (started, _) in list(self._budget.items()):
                if now - started >= 60:
                    self._budget.pop(old_actor)
            started, count = self._budget.get(actor, (now, 0))
            if now < started or count >= 20 or (actor not in self._budget and len(self._budget) >= 128):
                raise CancellationRateLimited() from None
            self._budget[actor] = (started, count + 1)

    def _tenant(self, tenant_id: str, revision: int):
        try:
            response = self.state.client.get_item(TableName=self.tenant_table,
                Key={"tenant_id": {"S": tenant_id}}, ConsistentRead=True)
            item = response.get("Item") if isinstance(response, dict) else None
            if (not isinstance(item, dict) or item.get("tenant_id") != {"S": tenant_id}
                    or item.get("revision") != {"N": str(revision)}):
                raise CancellationConflict()
            document = item["record"]["S"]
            if not isinstance(document, str) or len(document.encode("utf-8")) > 350000:
                raise CancellationUnavailable()
            _strict_provider_json(document.encode("utf-8"))
            record = from_item(item)
            if (record.tenant_id != tenant_id or record.revision != revision
                    or record.demo is not False or record.billing_mode.value != "stripe"
                    or not provider_id(record.payer_id, "cus")
                    or not provider_id(record.stripe_subscription_id, "sub")):
                raise CancellationConflict()
            binding = StripeBinding(tenant_id, record.payer_id, record.stripe_subscription_id)
            return binding, document
        except CancellationConflict:
            raise
        except Exception:
            raise CancellationUnavailable() from None

    @staticmethod
    def action_id(actor: str, tenant_id: str, reference: str) -> str:
        if not isinstance(reference, str) or not IDEMPOTENCY.fullmatch(reference):
            raise ValueError("Invalid action reference")
        # A reused action key cannot silently become another action merely by
        # changing the tenant/target. Those values belong in the request hash.
        return digest([CONTRACT, actor, reference])

    @staticmethod
    def _aad(actor: str, tenant_id: str) -> bytes:
        return canonical([CONTRACT, actor, tenant_id]).encode("ascii")

    def _encode(self, value: dict, actor: str, tenant_id: str) -> str:
        nonce = secrets.token_bytes(12)
        encrypted = self._cipher.encrypt(nonce, canonical(value).encode("ascii"), self._aad(actor, tenant_id))
        return "sc1." + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii").rstrip("=")

    def _decode(self, token: str, actor: str, tenant_id: str, now: int) -> dict:
        try:
            if not isinstance(token, str) or not re.fullmatch(r"sc1\.[A-Za-z0-9_-]{40,4092}", token):
                raise ValueError()
            text = token[4:]
            raw = base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != text:
                raise ValueError()
            value = json.loads(self._cipher.decrypt(raw[:12], raw[12:], self._aad(actor, tenant_id)))
            if (not isinstance(value, dict) or set(value) != {
                    "version", "revision", "record_hash", "provider_hash", "target_id", "control_hash",
                    "effective_at", "issued", "expires"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or any(type(value[k]) is not int for k in ("revision", "effective_at", "issued", "expires"))
                    or not 1 <= value["revision"] <= MAX_REVISION
                    or not 0 < value["issued"] <= now < value["expires"] <= value["effective_at"] <= 253402300799
                    or value["expires"] != min(value["issued"] + self.PREVIEW_SECONDS, value["effective_at"])
                    or any(not isinstance(value[k], str) or not re.fullmatch(r"[a-f0-9]{64}", value[k])
                           for k in ("record_hash", "provider_hash", "target_id", "control_hash"))):
                raise ValueError()
            return value
        except Exception:
            raise CancellationConflict() from None

    def preview(self, *, actor: str, tenant_id: str, expected_revision: int) -> dict:
        self._identity(actor, tenant_id, expected_revision)
        if self.provider is None:
            raise CancellationUnavailable() from None
        now = self._now()
        self._reserve_read(actor, now)
        binding, document = self._tenant(tenant_id, expected_revision)
        try:
            target_id = self.provider.target_id(binding.subscription_id)
            previous_control = self.state.read("control", target_id)
            snapshot = self.provider.inspect(binding, scheduled=False, now=now)
            observed = self._now()
            if observed >= snapshot["period_end"]:
                raise CancellationConflict()
            value = {"version": 1, "revision": expected_revision,
                "record_hash": hashlib.sha256(document.encode("utf-8")).hexdigest(),
                "provider_hash": digest(snapshot), "target_id": target_id,
                "control_hash": digest(previous_control),
                "effective_at": snapshot["period_end"], "issued": observed,
                "expires": min(observed + self.PREVIEW_SECONDS, snapshot["period_end"])}
            return {"status": "reviewed", "operation": CONTRACT, "tenant_id": tenant_id,
                "expected_revision": expected_revision, "observed_at_utc": utc(observed),
                "effective_at_utc": utc(value["effective_at"]), "expires_at_utc": utc(value["expires"]),
                "target_fingerprint": value["target_id"], "preview_reference": self._encode(value, actor, tenant_id),
                "confirmation": "SCHEDULE CANCELLATION " + tenant_id,
                "scheduling_available": new_cancellation_state_enabled(), "tenant_mutated": False}
        except CancellationConflict:
            raise
        except Exception:
            raise CancellationUnavailable() from None

    def receipt(self, *, actor: str, tenant_id: str, idempotency_key: str) -> dict:
        self._identity(actor, tenant_id)
        action_id = self.action_id(actor, tenant_id, idempotency_key)
        try:
            intent, outcome = self.state.history(action_id)
            if intent is not None and (intent["actor_sub"] != actor or intent["tenant_id"] != tenant_id):
                raise CancellationStateUnavailable()
            status = "not_found" if intent is None else outcome["status"] if outcome else "indeterminate"
            return {"operation": CONTRACT, "tenant_id": tenant_id, "action_reference": idempotency_key,
                "status": status, "integrity_status": "signed_verified" if intent else "not_found",
                "tenant_mutated": False, "provider_state_refreshed": False,
                "intent": None if intent is None else {
                    "action_id": action_id, "intent_hash": intent_hash(intent),
                    "actor_sub": intent["actor_sub"], "tenant_id": intent["tenant_id"],
                    "expected_revision": intent["expected_revision"], "reason": intent["reason"],
                    "generation": intent["generation"], "target_fingerprint": intent["target_id"],
                    "effective_at_utc": utc(intent["effective_at"]), "created_at_utc": utc(intent["created_at"]),
                    "before": False, "after": True},
                "outcome": None if outcome is None else {
                    "action_id": outcome["action_id"], "intent_hash": outcome["intent_hash"],
                    "status": outcome["status"], "recorded_at_utc": utc(outcome["recorded_at"])} }
        except Exception:
            raise CancellationUnavailable() from None

    def apply(self, *, actor: str, tenant_id: str, expected_revision: int,
              preview_reference: str, confirmation: str, reason: str, idempotency_key: str,
              execution_budget_seconds: float = 8.0) -> dict:
        if not new_cancellation_state_enabled() or self.provider is None:
            raise CancellationUnavailable() from None
        if (isinstance(execution_budget_seconds, bool) or not isinstance(execution_budget_seconds, (int,float))
                or not math.isfinite(execution_budget_seconds) or not 0 < execution_budget_seconds <= 8):
            raise CancellationUnavailable() from None
        deadline = time.monotonic() + execution_budget_seconds
        self._identity(actor, tenant_id, expected_revision)
        if (confirmation != "SCHEDULE CANCELLATION " + tenant_id
                or not isinstance(reason, str) or not REASON.fullmatch(reason)
                or not isinstance(preview_reference, str) or len(preview_reference) > 4096):
            raise ValueError("Invalid or unconfirmed cancellation")
        action_id = self.action_id(actor, tenant_id, idempotency_key)
        request_hash = digest([actor, tenant_id, expected_revision, preview_reference, confirmation, reason])
        try:
            old_intent, _ = self.state.history(action_id)
            if old_intent is not None:
                if old_intent["request_hash"] != request_hash:
                    raise CancellationConflict()
                return self.receipt(actor=actor, tenant_id=tenant_id, idempotency_key=idempotency_key)
        except CancellationConflict:
            raise
        except Exception:
            raise CancellationUnavailable() from None
        if deadline - time.monotonic() < 4:
            raise CancellationUnavailable() from None
        now = self._now()
        review = self._decode(preview_reference, actor, tenant_id, now)
        if review["revision"] != expected_revision:
            raise CancellationConflict() from None
        binding, document = self._tenant(tenant_id, expected_revision)
        if (hashlib.sha256(document.encode("utf-8")).hexdigest() != review["record_hash"]
                or self.provider.target_id(binding.subscription_id) != review["target_id"]):
            raise CancellationConflict() from None
        try:
            if digest(self.state.read("control", review["target_id"])) != review["control_hash"]:
                raise CancellationConflict()
        except CancellationConflict:
            raise
        except Exception:
            raise CancellationUnavailable() from None
        self._reserve_read(actor, now)
        try:
            snapshot = self.provider.inspect(binding, scheduled=False, now=now, deadline=deadline - 1)
            if digest(snapshot) != review["provider_hash"] or self._now() >= review["expires"]:
                raise CancellationConflict()
            # One POST, two verification reads and outcome persistence must
            # have room before we create any new durable financial claim.
            if deadline - time.monotonic() < 4:
                raise CancellationUnavailable()
        except CancellationConflict:
            raise
        except Exception:
            raise CancellationUnavailable() from None
        control = intent = None
        try:
            control, intent = self.state.reserve({"action_id": action_id, "target_id": review["target_id"],
                "actor_sub": actor, "tenant_id": tenant_id, "expected_revision": expected_revision,
                "request_hash": request_hash, "provider_hash": review["provider_hash"],
                "reason": reason, "effective_at": review["effective_at"], "created_at": self._now(),
                "before": False, "after": True}, expected_control_hash=review["control_hash"])
            control = self.state.dispatching(control, tenant_table=self.tenant_table, tenant_id=tenant_id,
                expected_revision=expected_revision, original_document=document)
            dispatch_time = self._now()
            if dispatch_time >= review["expires"] or deadline - time.monotonic() < 4:
                raise CancellationUnavailable()
            # This is the only provider mutation call. Only a successful CAS
            # response above grants this worker permission; recovery cannot.
            provider_key = "guardoath-sc1-" + digest([action_id, review["target_id"], intent["generation"]])
            changed = self.provider.dispatch(binding, idempotency_key=provider_key, now=dispatch_time, deadline=deadline - 1)
            if digest(changed) != review["provider_hash"]:
                raise CancellationUnavailable()
            verified = self.provider.inspect(binding, scheduled=True, now=self._now(), deadline=deadline - 1)
            if digest(verified) != review["provider_hash"]:
                raise CancellationUnavailable()
            self.state.outcome(intent, "verified", self._now())
            self.state.finish(control, verified=True)
        except CancellationStateConflict:
            if control is None:
                raise CancellationConflict() from None
            self._record_uncertainty(control, intent)
        except Exception:
            self._record_uncertainty(control, intent)
        return self.receipt(actor=actor, tenant_id=tenant_id, idempotency_key=idempotency_key)

    def _record_uncertainty(self, control, intent) -> None:
        if control is None or intent is None:
            raise CancellationUnavailable() from None
        # Lost immutable outcome writes are recovered by history, never a
        # repeated POST. Failed recovery leaves the target conservatively held.
        try:
            saved_intent, saved_outcome = self.state.history(intent["action_id"])
            if saved_intent is None or intent_hash(saved_intent) != intent_hash(intent):
                raise CancellationStateUnavailable()
            if saved_outcome is None:
                self.state.outcome(intent, "indeterminate", self._now())
            self.state.finish(control, verified=saved_outcome is not None and saved_outcome["status"] == "verified")
        except Exception:
            pass
