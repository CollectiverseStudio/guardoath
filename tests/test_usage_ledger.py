"""Idempotent usage ledger tests, no Stripe network calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from guardoath.billing import BillingAccount, BillingMode, usage_event
from guardoath.usage_ledger import UsageLedger, UsageConflict


def event(tenant, check):
    return usage_event(BillingAccount(tenant, BillingMode.STRIPE, "payer-"+tenant),
                       check_id=check)


def test_duplicate_delivery_is_idempotent():
    ledger = UsageLedger()
    item = event("tenant-a", "check-1")
    assert ledger.record(item)
    assert not ledger.record(item)
    assert ledger.events_for("tenant-a") == (item,)


def test_cross_tenant_usage_is_isolated():
    ledger = UsageLedger()
    ledger.record(event("tenant-a", "same"))
    ledger.record(event("tenant-b", "same"))
    assert len(ledger.events_for("tenant-a")) == 1
    assert len(ledger.events_for("tenant-b")) == 1


def test_conflicting_replay_and_capacity_fail_closed():
    ledger = UsageLedger(max_events=1)
    item = event("tenant-a", "check-1")
    ledger.record(item)
    with pytest.raises(UsageConflict):
        ledger.record(replace(item, units=2))
    with pytest.raises(UsageConflict):
        ledger.record(event("tenant-a", "check-2"))


def test_concurrent_duplicate_usage_records_once():
    ledger = UsageLedger()
    item = event("tenant-a", "check-1")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(ledger.record, [item] * 100))
    assert sum(results) == 1
    assert len(ledger.events_for("tenant-a")) == 1
