"""Durable local usage ledger tests; no Stripe API calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from valinator.billing import BillingAccount, BillingMode, usage_event
from valinator.sqlite_usage_ledger import SqliteUsageLedger
from valinator.usage_ledger import UsageConflict


def event(tenant, check):
    return usage_event(BillingAccount(tenant, BillingMode.PARTNER, "tokatlas"),
                       check_id=check)


def test_usage_survives_reopen_and_is_tenant_isolated(tmp_path):
    path = tmp_path / "usage.sqlite3"
    first = SqliteUsageLedger(path)
    assert first.record(event("tenant-a", "check-1"))
    assert first.record(event("tenant-b", "check-1"))
    reopened = SqliteUsageLedger(path)
    assert not reopened.record(event("tenant-a", "check-1"))
    assert len(reopened.events_for("tenant-a")) == 1
    assert len(reopened.events_for("tenant-b")) == 1


def test_conflicting_replay_is_rejected(tmp_path):
    ledger = SqliteUsageLedger(tmp_path / "usage.sqlite3")
    original = event("tenant-a", "check-1")
    ledger.record(original)
    with pytest.raises(UsageConflict):
        ledger.record(replace(original, units=2))


def test_concurrent_sqlite_record_is_idempotent(tmp_path):
    path = tmp_path / "usage.sqlite3"
    ledger = SqliteUsageLedger(path)
    item = event("tenant-a", "check-1")
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(ledger.record, [item] * 30))
    assert sum(result) == 1
    assert len(SqliteUsageLedger(path).events_for("tenant-a")) == 1
