"""Safe per-tenant administrative usage reporting from the authoritative record.

This is *not* AWS Cost Explorer or a Stripe invoice. Missing counters and cost
figures stay unknown, never manufactured as zero. No raw request content or
API-key material is included in the response.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Callable

MONTH = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")


class UsageUnavailable(ValueError):
    """Authoritative counters are malformed or cannot be safely projected."""


def tenant_usage_report(record, *, clock: Callable[[], datetime] | None = None) -> dict:
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise UsageUnavailable("UTC clock required")
    now = now.astimezone(timezone.utc)
    month = now.strftime("%Y-%m")
    usage = getattr(record, "usage", None)
    allowance = getattr(record, "monthly_allowance", None)
    revision = getattr(record, "revision", None)
    overage = getattr(record, "overage", None)
    tenant_id = getattr(record, "tenant_id", None)
    if (not isinstance(tenant_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", tenant_id)
            or not isinstance(usage, dict) or len(usage) > 120
            or type(allowance) is not int or allowance < 0
            or type(revision) is not int or revision < 0
            or overage not in ("block", "allow_and_record")):
        raise UsageUnavailable("Invalid authoritative usage record")
    if any(not isinstance(key, str) or not MONTH.fullmatch(key)
           or type(value) is not int or value < 0
           for key, value in usage.items()):
        raise UsageUnavailable("Invalid stored usage counter")
    recent = [{"month_utc": key, "requests": usage[key]} for key in sorted(usage, reverse=True)[:12]]
    count = usage.get(month)
    # On-demand quota signal from persisted month-to-date counters only. Never
    # call this an AWS cost alert, predicted bill or persistent notification.
    if count is None:
        signal = "unavailable"
    elif count > allowance:
        signal = "over_limit"
    elif count == allowance:
        signal = "at_limit"
    elif count * 100 >= allowance * 95:
        signal = "near_limit"
    elif count * 100 >= allowance * 80:
        signal = "watch"
    else:
        signal = "within_limit"
    threshold = {
        "state": signal,
        "utilization_percent_floor": count * 100 // allowance if count is not None and allowance else None,
        "policy": "blocks_at_saved_cap" if overage == "block" else "records_overage",
        "basis": "recorded current-month requests vs saved monthly allowance",
        "persisted_alert": False,
    }
    return {
        "tenant_id": tenant_id,
        "period_utc": month,
        "requests_recorded": count,  # None means no authoritative counter, not zero.
        "monthly_allowance": allowance,
        "remaining_before_allowance": max(0, allowance - count) if count is not None else None,
        "overage_policy": overage,
        "blocking_cap_enabled": overage == "block",
        "usage_threshold": threshold,
        "recent_recorded_months": recent,
        "source": "authoritative GuardOath tenant usage record",
        "record_revision": revision,
        "record_updated_at": getattr(record, "updated_at", None),
        "read_at_utc": now.isoformat(timespec="seconds"),
        "aws_cost_usd": None,
        "aws_cost_status": "unavailable (no verified AWS billing allocation)",
        "stripe_invoice_usd": None,
        "stripe_invoice_status": "unavailable (no provider invoice verification)",
        "note": "Usage is recorded request count, not a live AWS charge or verified invoice",
    }
