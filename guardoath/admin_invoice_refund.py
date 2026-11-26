"""Reviewed full invoice refunds with permanent claims and read-only recovery.

The owner selects an opaque invoice reference. Provider identifiers, amounts,
mode and customer authority come from verified server observations. A historical
receipt or refresh can never acquire permission to issue another refund POST.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import hmac
import math
import re
import secrets
from threading import Lock
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from guardoath.admin_actions import IDEMPOTENCY, REASON, TENANT
from guardoath.admin_audit import SUBJECT
from guardoath.admin_cancellation_provider import canonical, digest, provider_id
from guardoath.admin_invoices import InvoiceRefundReferences, InvalidInvoiceReference
from guardoath.admin_refund_provider import StripeInvoiceRefund, SNAPSHOT_FIELDS, MAX_AMOUNT
from guardoath.admin_refund_state import (
    CONTRACT, MAX_REVISION, DynamoRefundState, RefundStateConflict,
    RefundStateUnavailable, intent_hash,
)
from guardoath.dynamodb_store import from_item
from guardoath.state_contracts import new_refund_state_enabled
from guardoath.stripe_provider import _strict_provider_json
from guardoath.subscription_reconciliation import StripeBinding


class RefundConflict(Exception):
    """The selected invoice, review or authoritative tenant changed."""


class RefundUnavailable(Exception):
    """No authoritative refund result is available; retain its reference."""


class RefundRateLimited(Exception):
    pass


# A bounded display map, not an ISO-currency fallback. Stripe charge/refund
# units differ from payout units for HUF/TWD and from ISO fractions for ISK/UGX.
# https://docs.stripe.com/currencies#special-cases
# Unsupported currencies refuse review before any financial state is written.
_TWO_DECIMAL = frozenset("usd aed ars aud brl cad chf cop czk dkk eur gbp hkd huf "
                        "idr ils inr mxn myr nok nzd php pln ron rub sek sgd thb "
                        "twd zar isk ugx".split())
_ZERO_DECIMAL = frozenset({"jpy", "krw"})


def currency_exponent(currency: str) -> int:
    if currency in _ZERO_DECIMAL:
        return 0
    if currency in _TWO_DECIMAL:
        return 2
    raise RefundUnavailable() from None


def utc(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AdminInvoiceRefund:
    PREVIEW_SECONDS = 180

    def __init__(self, *, state: DynamoRefundState, tenant_table: str,
                 preview_key: bytes, invoice_references: InvoiceRefundReferences | None = None,
                 provider: StripeInvoiceRefund | None = None, clock=time.time):
        if (not isinstance(state, DynamoRefundState)
                or not isinstance(tenant_table, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", tenant_table)
                or tenant_table == state.table_name
                or not isinstance(preview_key, bytes) or len(preview_key) < 32
                or (invoice_references is not None
                    and not isinstance(invoice_references, InvoiceRefundReferences))
                or (provider is not None and
                    (not isinstance(provider, StripeInvoiceRefund)
                     or invoice_references is None
                     or invoice_references.expected_price_id != provider.expected_price_id
                     or invoice_references.expected_livemode is not provider.expected_livemode))
                or not callable(clock)):
            raise ValueError("Independent owner invoice-refund configuration required")
        self.state, self.tenant_table = state, tenant_table
        self.provider, self.invoice_references = provider, invoice_references
        self._clock = clock
        self._cipher = AESGCM(hmac.new(preview_key, b"guardoath-invoice-refund-preview-v1",
                                      hashlib.sha256).digest())
        self._budget, self._lock = OrderedDict(), Lock()

    def _now(self) -> int:
        now = self._clock()
        if (isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now)
                or not 946684800 <= now < 4102444800 - self.PREVIEW_SECONDS):
            raise RefundUnavailable() from None
        return int(now)

    @staticmethod
    def _identity(actor: str, tenant_id: str, expected_revision: int | None = None) -> None:
        if (not isinstance(actor, str) or not SUBJECT.fullmatch(actor)
                or not isinstance(tenant_id, str) or not TENANT.fullmatch(tenant_id)
                or (expected_revision is not None and
                    (type(expected_revision) is not int or not 1 <= expected_revision <= MAX_REVISION))):
            raise ValueError("Invalid invoice-refund context")

    def _reserve_read(self, actor: str, now: int) -> None:
        with self._lock:
            for previous_actor, (started, _) in list(self._budget.items()):
                if now - started >= 60:
                    self._budget.pop(previous_actor)
            started, count = self._budget.get(actor, (now, 0))
            if now < started or count >= 20 or (actor not in self._budget and len(self._budget) >= 128):
                raise RefundRateLimited() from None
            self._budget[actor] = (started, count + 1)

    def _tenant(self, tenant_id: str, revision: int):
        try:
            response = self.state.client.get_item(TableName=self.tenant_table,
                Key={"tenant_id": {"S": tenant_id}}, ConsistentRead=True)
            item = response.get("Item") if isinstance(response, dict) else None
            if (not isinstance(item, dict) or item.get("tenant_id") != {"S": tenant_id}
                    or item.get("revision") != {"N": str(revision)}):
                raise RefundConflict()
            document = item["record"]["S"]
            if not isinstance(document, str) or len(document.encode("utf-8")) > 350000:
                raise RefundUnavailable()
            _strict_provider_json(document.encode("utf-8"))
            record = from_item(item)
            if (record.tenant_id != tenant_id or record.revision != revision
                    or record.demo is not False or record.billing_mode.value != "stripe"
                    or not provider_id(record.payer_id, "cus")
                    or not provider_id(record.stripe_subscription_id, "sub")):
                raise RefundConflict()
            return record, StripeBinding(tenant_id, record.payer_id, record.stripe_subscription_id), document
        except RefundConflict:
            raise
        except Exception:
            raise RefundUnavailable() from None

    @staticmethod
    def action_id(actor: str, tenant_id: str, reference: str) -> str:
        if not isinstance(reference, str) or not IDEMPOTENCY.fullmatch(reference):
            raise ValueError("Invalid action reference")
        return digest([CONTRACT, actor, reference])

    @staticmethod
    def _aad(actor: str, tenant_id: str) -> bytes:
        return canonical([CONTRACT, actor, tenant_id]).encode("ascii")

    @staticmethod
    def _invoice_fingerprint(snapshot: dict) -> str:
        return digest([CONTRACT, "invoice", snapshot["account_id"],
                       snapshot["livemode"], snapshot["invoice_id"]])

    @staticmethod
    def _confirmation(tenant_id: str, snapshot: dict) -> str:
        return "REFUND " + tenant_id + " " + str(snapshot["amount_minor"]) + " " + snapshot["currency"].upper() + " MINOR UNITS"

    @staticmethod
    def _snapshot(value: dict) -> dict:
        if (not isinstance(value, dict) or set(value) != SNAPSHOT_FIELDS
                or any(not provider_id(value[field], prefix) for field, prefix in (
                    ("account_id", "acct"), ("customer_id", "cus"), ("subscription_id", "sub"),
                    ("invoice_id", "in"), ("charge_id", "ch"), ("payment_intent_id", "pi"),
                    ("expected_price_id", "price")))
                or type(value["livemode"]) is not bool
                or type(value["amount_minor"]) is not int or not 1 <= value["amount_minor"] <= MAX_AMOUNT
                or not isinstance(value["currency"], str)
                or not re.fullmatch(r"[a-z]{3}", value["currency"])):
            raise RefundUnavailable() from None
        currency_exponent(value["currency"])
        return dict(value)

    def _provider_binding(self, snapshot: dict, *, historical: bool = False) -> None:
        if (self.provider is None
                or snapshot["account_id"] != self.provider.expected_account_id
                or snapshot["livemode"] is not self.provider.expected_livemode
                or (not historical and snapshot["expected_price_id"] != self.provider.expected_price_id)):
            raise RefundConflict() from None

    def _encode(self, value: dict, actor: str, tenant_id: str) -> str:
        nonce = secrets.token_bytes(12)
        sealed = self._cipher.encrypt(nonce, canonical(value).encode("ascii"), self._aad(actor, tenant_id))
        return "rf1." + base64.urlsafe_b64encode(nonce + sealed).decode("ascii").rstrip("=")

    def _decode(self, token: str, actor: str, tenant_id: str, now: int) -> dict:
        try:
            if not isinstance(token, str) or not re.fullmatch(r"rf1\.[A-Za-z0-9_-]{40,4092}", token):
                raise ValueError()
            encoded = token[4:]
            raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != encoded:
                raise ValueError()
            value = _strict_provider_json(self._cipher.decrypt(raw[:12], raw[12:], self._aad(actor, tenant_id)))
            if (set(value) != {"version", "revision", "record_hash", "provider_hash", "target_id",
                               "control_hash", "snapshot", "issued", "expires"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or any(type(value[k]) is not int for k in ("revision", "issued", "expires"))
                    or not 1 <= value["revision"] <= MAX_REVISION
                    or not 946684800 <= value["issued"] <= now < value["expires"] <= 4102444800
                    or value["expires"] != value["issued"] + self.PREVIEW_SECONDS
                    or any(not isinstance(value[k], str) or not re.fullmatch(r"[a-f0-9]{64}", value[k])
                           for k in ("record_hash", "provider_hash", "target_id", "control_hash"))
                    or digest(self._snapshot(value["snapshot"])) != value["provider_hash"]):
                raise ValueError()
            return value
        except Exception:
            raise RefundConflict() from None

    def preview(self, *, actor: str, tenant_id: str, expected_revision: int,
                invoice_reference: str) -> dict:
        self._identity(actor, tenant_id, expected_revision)
        if self.provider is None or self.invoice_references is None:
            raise RefundUnavailable() from None
        if not isinstance(invoice_reference, str) or not re.fullmatch(r"ir1\.[A-Za-z0-9_-]{40,2044}", invoice_reference):
            raise RefundConflict() from None
        now = self._now()
        self._reserve_read(actor, now)
        record, binding, document = self._tenant(tenant_id, expected_revision)
        try:
            invoice_id = self.invoice_references.open(invoice_reference, record, actor=actor, now=now)
            snapshot = self._snapshot(self.provider.inspect(binding, invoice_id, now=now))
            self._provider_binding(snapshot)
            target_id = self.provider.target_id(snapshot["charge_id"])
            control = self.state.read("control", target_id)
            if control is not None:
                raise RefundConflict()
            observed = self._now()
            if observed < now:
                raise RefundUnavailable()
            value = {"version": 1, "revision": expected_revision,
                "record_hash": hashlib.sha256(document.encode("utf-8")).hexdigest(),
                "provider_hash": digest(snapshot), "target_id": target_id,
                "control_hash": digest(None), "snapshot": snapshot,
                "issued": observed, "expires": observed + self.PREVIEW_SECONDS}
            return {"status": "reviewed", "operation": CONTRACT, "tenant_id": tenant_id,
                "expected_revision": expected_revision, "observed_at_utc": utc(observed),
                "expires_at_utc": utc(value["expires"]), "amount_minor": snapshot["amount_minor"],
                "currency": snapshot["currency"], "currency_exponent": currency_exponent(snapshot["currency"]),
                "livemode": snapshot["livemode"], "invoice_fingerprint": self._invoice_fingerprint(snapshot),
                "target_fingerprint": target_id, "preview_reference": self._encode(value, actor, tenant_id),
                "confirmation": self._confirmation(tenant_id, snapshot),
                "refund_available": new_refund_state_enabled(), "tenant_mutated": False}
        except (RefundConflict, InvalidInvoiceReference):
            raise RefundConflict() from None
        except Exception:
            raise RefundUnavailable() from None

    def receipt(self, *, actor: str, tenant_id: str, idempotency_key: str) -> dict:
        self._identity(actor, tenant_id)
        action_id = self.action_id(actor, tenant_id, idempotency_key)
        try:
            intent, outcome = self.state.history(action_id)
            if intent is not None and (intent["actor_sub"] != actor or intent["tenant_id"] != tenant_id):
                raise RefundStateUnavailable()
            status = "not_found" if intent is None else outcome["status"] if outcome else "indeterminate"
            return {"operation": CONTRACT, "tenant_id": tenant_id, "action_reference": idempotency_key,
                "status": status, "integrity_status": "signed_verified" if intent else "not_found",
                "tenant_mutated": False, "provider_state_refreshed": False,
                "intent": None if intent is None else {
                    "action_id": action_id, "intent_hash": intent_hash(intent),
                    "actor_sub": intent["actor_sub"], "tenant_id": intent["tenant_id"],
                    "expected_revision": intent["expected_revision"], "reason": intent["reason"],
                    "generation": intent["generation"], "target_fingerprint": intent["target_id"],
                    "invoice_fingerprint": self._invoice_fingerprint(intent),
                    "amount_minor": intent["amount_minor"], "currency": intent["currency"],
                    "currency_exponent": currency_exponent(intent["currency"]),
                    "livemode": intent["livemode"], "created_at_utc": utc(intent["created_at"]),
                    "before": 0, "after": intent["amount_minor"]},
                "outcome": None if outcome is None else {
                    "action_id": outcome["action_id"], "intent_hash": outcome["intent_hash"],
                    "status": outcome["status"],
                    "provider_status": outcome["proof"]["status"] if outcome["proof"] else None,
                    "recorded_at_utc": utc(outcome["recorded_at"])} }
        except Exception:
            raise RefundUnavailable() from None

    @staticmethod
    def _deadline(seconds: float) -> float:
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or not 0 < seconds <= 8):
            raise RefundUnavailable() from None
        return time.monotonic() + seconds

    @staticmethod
    def _proof_status(proof: dict) -> str:
        try:
            return {"succeeded": "verified", "pending": "pending", "requires_action": "pending",
                    "failed": "failed", "canceled": "failed"}[proof["status"]]
        except Exception:
            raise RefundUnavailable() from None

    def apply(self, *, actor: str, tenant_id: str, expected_revision: int,
              preview_reference: str, confirmation: str, reason: str, idempotency_key: str,
              execution_budget_seconds: float = 8.0) -> dict:
        self._identity(actor, tenant_id, expected_revision)
        if not new_refund_state_enabled() or self.provider is None:
            raise RefundUnavailable() from None
        deadline = self._deadline(execution_budget_seconds)
        if (not isinstance(reason, str) or not REASON.fullmatch(reason)
                or not isinstance(confirmation, str) or len(confirmation) > 180
                or not isinstance(preview_reference, str) or len(preview_reference) > 4096):
            raise ValueError("Invalid or unconfirmed invoice refund")
        action_id = self.action_id(actor, tenant_id, idempotency_key)
        request_hash = digest([actor, tenant_id, expected_revision, preview_reference, confirmation, reason])
        try:
            old_intent, _ = self.state.history(action_id)
            if old_intent is not None:
                if old_intent["request_hash"] != request_hash or old_intent["tenant_id"] != tenant_id:
                    raise RefundConflict()
                return self.receipt(actor=actor, tenant_id=tenant_id, idempotency_key=idempotency_key)
        except RefundConflict:
            raise
        except Exception:
            raise RefundUnavailable() from None
        if deadline - time.monotonic() < 4:
            raise RefundUnavailable() from None
        now = self._now()
        review = self._decode(preview_reference, actor, tenant_id, now)
        snapshot = review["snapshot"]
        if (review["revision"] != expected_revision
                or confirmation != self._confirmation(tenant_id, snapshot)):
            raise RefundConflict() from None
        self._provider_binding(snapshot)
        _, binding, document = self._tenant(tenant_id, expected_revision)
        if (hashlib.sha256(document.encode("utf-8")).hexdigest() != review["record_hash"]
                or binding.customer_id != snapshot["customer_id"]
                or binding.subscription_id != snapshot["subscription_id"]
                or self.provider.target_id(snapshot["charge_id"]) != review["target_id"]):
            raise RefundConflict() from None
        try:
            control = self.state.read("control", review["target_id"])
            if control is not None or digest(control) != review["control_hash"]:
                raise RefundConflict()
            self._reserve_read(actor, now)
            fresh = self.provider.inspect(binding, snapshot["invoice_id"], now=now, deadline=deadline - 1)
            if digest(fresh) != review["provider_hash"] or self._now() >= review["expires"]:
                raise RefundConflict()
            if deadline - time.monotonic() < 4:
                raise RefundUnavailable()
        except (RefundConflict, RefundRateLimited):
            raise
        except Exception:
            raise RefundUnavailable() from None
        intent = None
        try:
            control, intent = self.state.reserve({**snapshot, "action_id": action_id,
                "target_id": review["target_id"], "actor_sub": actor, "tenant_id": tenant_id,
                "expected_revision": expected_revision, "request_hash": request_hash,
                "provider_hash": review["provider_hash"], "reason": reason, "created_at": self._now()},
                expected_control_hash=review["control_hash"])
            self.state.dispatching(control, tenant_table=self.tenant_table, tenant_id=tenant_id,
                expected_revision=expected_revision, original_document=document)
            dispatch_time = self._now()
            if dispatch_time >= review["expires"] or deadline - time.monotonic() < 4:
                raise RefundUnavailable()
            # Only the acknowledged dispatch CAS above permits this single POST.
            # Idempotency is a second provider safeguard, never permission to retry.
            provider_key = "guardoath-ir1-" + action_id
            submitted = self.provider.dispatch(snapshot, action_id=action_id,
                idempotency_key=provider_key, now=dispatch_time, deadline=deadline - 1)
            proof = self.provider.verify(snapshot, action_id=action_id,
                refund_id=submitted["refund_id"], now=self._now(), deadline=deadline - 1)
            if any(proof[field] != submitted[field] for field in
                   (*SNAPSHOT_FIELDS, "action_id", "refund_id", "refund_created")):
                raise RefundUnavailable()
            self.state.outcome(intent, self._proof_status(proof), self._now(), proof=proof)
        except RefundStateConflict:
            if intent is None:
                raise RefundConflict() from None
            self._record_uncertainty(intent)
        except Exception:
            self._record_uncertainty(intent)
        return self.receipt(actor=actor, tenant_id=tenant_id, idempotency_key=idempotency_key)

    def _record_uncertainty(self, intent) -> None:
        if intent is None:
            raise RefundUnavailable() from None
        try:
            saved_intent, saved_outcome = self.state.history(intent["action_id"])
            if saved_intent is None or intent_hash(saved_intent) != intent_hash(intent):
                raise RefundStateUnavailable()
            if saved_outcome is None:
                self.state.outcome(intent, "indeterminate", self._now(), proof=None)
        except Exception:
            # An unknown receipt write never releases the financial claim.
            pass

    def refresh(self, *, actor: str, tenant_id: str, idempotency_key: str,
                execution_budget_seconds: float = 8.0) -> dict:
        self._identity(actor, tenant_id)
        if not new_refund_state_enabled() or self.provider is None:
            raise RefundUnavailable() from None
        deadline = self._deadline(execution_budget_seconds)
        action_id = self.action_id(actor, tenant_id, idempotency_key)
        try:
            intent, previous = self.state.history(action_id)
            if intent is None:
                return self.receipt(actor=actor, tenant_id=tenant_id, idempotency_key=idempotency_key)
            if intent["actor_sub"] != actor or intent["tenant_id"] != tenant_id:
                raise RefundStateUnavailable()
            if previous is not None and previous["status"] in {"verified", "failed"}:
                return self.receipt(actor=actor, tenant_id=tenant_id, idempotency_key=idempotency_key)
            now = self._now()
            self._reserve_read(actor, now)
            snapshot = self._snapshot({field: intent[field] for field in SNAPSHOT_FIELDS})
            self._provider_binding(snapshot, historical=True)
            refund_id = previous["proof"]["refund_id"] if previous and previous["proof"] else None
            # Original signed authority is retained even if ordinary usage has
            # advanced the tenant revision. This operation can only read Stripe.
            proof = self.provider.verify(snapshot, action_id=action_id,
                refund_id=refund_id, now=now, deadline=deadline - 1)
            if previous is None:
                self.state.outcome(intent, self._proof_status(proof), self._now(), proof=proof)
            else:
                self.state.observe(intent, self._proof_status(proof), self._now(), proof=proof)
            result = self.receipt(actor=actor, tenant_id=tenant_id, idempotency_key=idempotency_key)
            result["provider_state_refreshed"] = True
            return result
        except RefundRateLimited:
            raise
        except Exception:
            raise RefundUnavailable() from None
