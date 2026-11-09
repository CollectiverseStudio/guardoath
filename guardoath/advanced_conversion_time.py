"""FC-035/042/043/050: bounded conversion and temporal invariants in the existing
cross_field Engine. Reference FX ratios are tenant-saved, versioned policy inputs,
not live/externally attested rates and never derived from submitted documents.
"""
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
import re

from guardoath.advanced_arithmetic import _number
from guardoath.business import resolve
from guardoath.models import Finding

_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:[.][0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)
_MAX_DURATION = timedelta(days=366)
# The international avoirdupois pound is exactly 0.45359237 kg.
_LB_TO_KG = Decimal("0.45359237")


def _time(value):
    if not isinstance(value, str) or len(value) > 35 or not _TIMESTAMP.fullmatch(value):
        raise ValueError("invalid_offset_timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timezone_required")
    return parsed


def _duration(start, end):
    difference = _time(end) - _time(start)
    if not timedelta(0) <= difference <= _MAX_DURATION:
        raise ValueError("invalid_duration")
    # Integer microseconds, no float rounding or implicit local timezone.
    return ((difference.days * 86400 + difference.seconds) * 1000000 +
            difference.microseconds)


def _valid(rule, payload):
    values = {}
    for name, pointer in rule.fields.items():
        try:
            values[name] = resolve(payload, pointer)
        except (KeyError, IndexError):
            # A return may be absent before any return event: object-key
            # absence raises KeyError, while an unpopulated JSON-array slot
            # (for example /events/3/at on a three-event timeline) raises
            # IndexError. Only the optional return field may be absent;
            # required order/shipment/delivery fields still fail closed.
            if rule.kind == "lifecycle_chronology" and name == "return_at":
                continue
            raise
    with localcontext() as ctx:
        ctx.prec, ctx.Emax, ctx.Emin = 128, 128, -128
        if rule.kind == "currency_conversion_cap":
            source, destination = values["payment_currency"], values["invoice_currency"]
            if (not isinstance(source, str) or not isinstance(destination, str) or
                    source != rule.source_currency or destination != rule.destination_currency):
                return False
            payment = _number(values["payment_amount"], nonnegative=True)
            invoice = _number(values["invoice_amount"], nonnegative=True)
            # Multiply exact bounded integer ratio; do not round a currency conversion.
            return (payment * rule.trusted_rate_numerator <=
                    invoice * rule.trusted_rate_denominator)
        if rule.kind == "unit_load_kg_cap":
            unit = values["load_unit"]
            if not isinstance(unit, str) or unit not in ("kg", "lb"):
                return False
            load = _number(values["load"], nonnegative=True)
            limit = _number(values["max_kg"], nonnegative=True)
            normalized = load if unit == "kg" else load * _LB_TO_KG
            return normalized <= limit
        if rule.kind == "cold_chain_excursion":
            minutes = values["allowed_minutes"]
            if type(minutes) is not int or not 0 <= minutes <= 366 * 24 * 60:
                return False
            return (_duration(values["excursion_start"], values["excursion_end"])
                    <= minutes * 60 * 1000000)
        if rule.kind == "lifecycle_chronology":
            ordered = [_time(values[name]) for name in
                       ("order_at", "shipment_at", "delivery_at") +
                       (("return_at",) if "return_at" in values else ())]
            return all(before <= after for before, after in zip(ordered, ordered[1:]))
    return False


def conversion_time_findings(payload, rules, stage):
    """Return all failures with stable rule IDs and no submitted/reference data."""
    findings = []
    for rule in rules:
        if stage not in rule.stages:
            continue
        try:
            valid = _valid(rule, payload)
        except (ValueError, KeyError, IndexError, TypeError, OverflowError, ArithmeticError):
            valid = False
        if not valid:
            findings.append(Finding(
                rule_id="business." + rule.rule_id,
                category="business", severity="high",
                explanation="Configured conversion, bound, or temporal ordering constraint failed.",
            ))
    return tuple(findings)
