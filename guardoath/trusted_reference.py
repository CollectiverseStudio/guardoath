"""Host-attested tenant reference constraints FC-044/045/047/049.

No user-submitted reference limit, previous clinical administration or PO vendor
can become authority. The host signing key is never stored on the tenant or sent
to an AI service. A missing, expired or invalid reference makes the entire
evaluation incomplete/Block (including observe and failure_action=Flag).
Signing establishes host provenance, NOT independent medical/regulatory truth.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import hmac
import json
import os

from guardoath.advanced_arithmetic import _number
from guardoath.advanced_conversion_time import _time
from guardoath.advanced_identity import _identifier
from guardoath.business import resolve
from guardoath.models import Finding

SIGNING_KEY_ENV = "GUARDOATH_REFERENCE_SIGNING_KEY"
MAX_REFERENCE_LIFETIME = timedelta(days=366)
MAX_INTERVAL_MINUTES = 366 * 24 * 60


def _canonical(rule, tenant_id: str) -> bytes:
    # Tenant binding, complete rule configuration, reference snapshot, stages,
    # identity, version and expiry are all MAC-covered.
    payload = {"tenant_id": tenant_id,
               "rule": rule.model_dump(mode="json", exclude={"host_signature"})}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sign_reference(rule, tenant_id: str, key: str) -> str:
    """Operator-side signer. Only use after externally verifying the source data."""
    if not isinstance(key, str) or len(key) < 32:
        raise ValueError("Host signing key must contain at least 32 characters")
    return hmac.new(key.encode("utf-8"), _canonical(rule, tenant_id), hashlib.sha256).hexdigest()


def reference_is_trusted(rule, tenant_id: str, *, now: datetime | None = None, key: str | None = None) -> bool:
    key = os.environ.get(SIGNING_KEY_ENV) if key is None else key
    if not isinstance(key, str) or len(key) < 32:
        return False
    try:
        signed_at = _time(rule.verified_at).astimezone(timezone.utc)
        expires = _time(rule.expires_at).astimezone(timezone.utc)
        now = now or datetime.now(timezone.utc)
        if not signed_at <= now < expires or expires - signed_at > MAX_REFERENCE_LIFETIME:
            return False
        expected = sign_reference(rule, tenant_id, key)
        return hmac.compare_digest(rule.host_signature, expected)
    except (ValueError, TypeError, OverflowError):
        return False


def _reference_number(value, *, positive=False, nonnegative=False):
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("Invalid host numeric reference")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("Invalid host numeric reference") from None
    return _number(number, positive=positive, nonnegative=nonnegative)


def _valid(rule, payload):
    values = {name: resolve(payload, pointer) for name, pointer in rule.fields.items()}
    reference = rule.reference_values
    with localcontext() as context:
        context.prec, context.Emax, context.Emin = 128, 128, -128
        if rule.kind == "dose_ceiling":
            dose = _number(values["dose_mg"], nonnegative=True)
            verified_weight = _reference_number(reference["verified_weight_kg"], positive=True)
            max_per_kg = _reference_number(reference["max_mg_per_kg"], positive=True)
            return dose <= verified_weight * max_per_kg
        if rule.kind == "medication_interval":
            current = _time(values["administered_at"])
            previous = _time(reference["previous_administration"])
            minutes = int(reference["minimum_interval_minutes"])
            if (reference["minimum_interval_minutes"] != str(minutes)
                    or not 1 <= minutes <= MAX_INTERVAL_MINUTES):
                return False
            return current >= previous + timedelta(minutes=minutes)
        if rule.kind == "permit_cap":
            emission = _number(values["reported_emission"], nonnegative=True)
            return emission <= _reference_number(reference["permit_limit"], nonnegative=True)
        if rule.kind == "invoice_po_vendor":
            return (_identifier(values["invoice_vendor_id"]) ==
                    _identifier(reference["approved_vendor_id"]))
    return False


def trusted_reference_findings(payload, rules, stage, tenant_id: str):
    """All rule findings; no reference/source/request values appear in messages."""
    findings = []
    for rule in rules:
        if stage not in rule.stages:
            continue
        trusted = reference_is_trusted(rule, tenant_id)
        if trusted:
            try:
                valid = _valid(rule, payload)
            except (ValueError, TypeError, KeyError, IndexError, ArithmeticError, OverflowError):
                valid = False
        else:
            valid = False
        if not valid:
            findings.append(Finding(rule_id="business." + rule.rule_id, category="business",
                                    severity="high", explanation=(
                                        "A verified host reference is missing, invalid, or expired."
                                        if not trusted else
                                        "Configured host-reference consistency requirement failed."
                                    )))
    return tuple(findings)
