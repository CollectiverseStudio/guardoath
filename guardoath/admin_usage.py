"""Safe per-tenant administrative usage reporting from the authoritative record.

This is *not* AWS Cost Explorer or a Stripe invoice. Missing counters and cost
figures stay unknown, never manufactured as zero. No raw request content or
API-key material is included in the response.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timezone
import re
from typing import Callable

MONTH = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
UTC_RECORD = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
_JSON_SAFE_INTEGER = (1 << 53) - 1


class UsageUnavailable(ValueError):
    """Authoritative counters are malformed or cannot be safely projected."""


def _quota_pacing(count: int | None, allowance: int, overage: str,
                  record_updated_at: object, now: datetime) -> dict:
    """Advisory request pacing; no live billing, AWS cost or persistent alert."""
    freshness = "unavailable"
    if isinstance(record_updated_at, str) and UTC_RECORD.fullmatch(record_updated_at):
        try:
            updated = datetime.strptime(record_updated_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
            age_seconds = (now - updated).total_seconds()
            if -300 <= age_seconds <= 48 * 3600:
                freshness = "recent_record_metadata"
            elif age_seconds > 48 * 3600:
                freshness = "stale_record_metadata"
        except ValueError:
            pass

    total_days = monthrange(now.year, now.month)[1]
    # A recorded cap breach is a fact about the saved counter, not a
    # forecast. Do not hide it behind early-month or stale-metadata gates.
    # Keep the projection unavailable until its own freshness gate passes.
    raw_estimate = (count * total_days // now.day
                    if count is not None and allowance > 0 and
                    freshness == "recent_record_metadata" and now.day >= 7
                    else None)
    # Preserve exact integer semantics in the browser, including overflow.
    estimate = (raw_estimate if raw_estimate is None or
                raw_estimate <= _JSON_SAFE_INTEGER else None)
    if count is None:
        state = "unknown_counter"
    elif count > allowance:
        state = "already_over_limit"
    elif allowance == 0:
        state = "zero_allowance"
    elif count == allowance:
        state = "already_at_limit"
    elif freshness != "recent_record_metadata":
        state = "stale_or_unknown_metadata"
    elif now.day < 7:
        state = "insufficient_observation"
    elif estimate is None:
        state = "projection_out_of_range"
    elif estimate > allowance:
        state = "projected_over_limit"
    elif estimate * 100 >= allowance * 95:
        state = "projected_near_limit"
    elif estimate * 100 >= allowance * 80:
        state = "projected_watch"
    else:
        state = "projected_within_limit"
    return {
        "state": state,
        "projected_month_end_requests_floor": estimate,
        "record_metadata_freshness": freshness,
        "calendar_days_elapsed": now.day,
        "calendar_days_in_month": total_days,
        "enforcement": "blocks_at_saved_cap" if overage == "block" else "records_overage",
        "persisted_alert": False,
        "basis": "linear calendar-day extrapolation of stored current-month counter",
        "note": "Advisory recorded-request pacing only; record metadata does not prove "
                "counter completeness. Not live throughput, AWS costs, billing or alerts.",
    }


def _historical_volume_signal(usage: dict, now: datetime, pacing: dict) -> dict:
    """Advisory volume comparison, never a cost or a persistent alert.

    Compare a gated month-end projection with three consecutive preceding
    recorded UTC months. Missing month records are unknown, not zero.
    """
    projection = pacing["projected_month_end_requests_floor"]
    result = {
        "state": "unavailable", "history_months_utc": [],
        "history_median_monthly_requests": None,
        "projected_vs_history_percent_floor": None,
        "persisted_alert": False,
        "basis": "projected saved-request count vs median of 3 preceding UTC months",
        "note": "Advisory only; past records may be incomplete. "
                "Not AWS costs, Stripe billing, an alert or enforcement.",
    }
    if projection is None:
        return result
    month_index = now.year * 12 + now.month - 1
    months = []
    for offset in (1, 2, 3):
        previous = month_index - offset
        if previous < 12:
            result["state"] = "insufficient_history"
            return result
        months.append(f"{previous // 12:04d}-{previous % 12 + 1:02d}")
    if any(period not in usage for period in months):
        result["state"] = "insufficient_history"
        return result
    baseline = sorted(usage[period] for period in months)[1]
    result["history_months_utc"] = months
    result["history_median_monthly_requests"] = baseline
    if baseline < 30:
        result["state"] = "low_volume_history"
        return result
    ratio = projection * 100 // baseline
    if ratio > _JSON_SAFE_INTEGER:
        result["state"] = "projection_out_of_range"
        return result
    result["projected_vs_history_percent_floor"] = ratio
    if ratio >= 200:
        result["state"] = "possible_request_surge"
    elif ratio >= 150:
        result["state"] = "elevated_request_volume"
    elif ratio <= 50:
        result["state"] = "lower_request_volume"
    else:
        result["state"] = "within_historical_range"
    return result


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
           or type(value) is not int or not 0 <= value <= _JSON_SAFE_INTEGER
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
    percent = count * 100 // allowance if count is not None and allowance else None
    if percent is not None and percent > _JSON_SAFE_INTEGER:
        percent = None
    threshold = {
        "state": signal,
        "utilization_percent_floor": percent,
        "policy": "blocks_at_saved_cap" if overage == "block" else "records_overage",
        "basis": "recorded current-month requests vs saved monthly allowance",
        "persisted_alert": False,
    }
    pacing = _quota_pacing(count, allowance, overage,
                           getattr(record, "updated_at", None), now)
    history = _historical_volume_signal(usage, now, pacing)
    return {
        "tenant_id": tenant_id,
        "period_utc": month,
        "requests_recorded": count,  # None means no authoritative counter, not zero.
        "monthly_allowance": allowance,
        "remaining_before_allowance": max(0, allowance - count) if count is not None else None,
        "overage_policy": overage,
        "blocking_cap_enabled": overage == "block",
        "usage_threshold": threshold,
        "quota_pacing": pacing,
        "historical_volume": history,
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
