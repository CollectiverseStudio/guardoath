"""Regression for issue #30: all four PII workers must be primed before a timed scan.

These tests exercise the real ThreadPoolExecutor and never use customer data,
increase the shipped 500-ms deadline or bypass admission. Startup probes must
not take a scan slot; parallel startup must finish without deadlocking.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, get_ident

import guardoath.pii as pii


class _ForbiddenScanAdmission:
    def acquire(self, *args, **kwargs):
        raise AssertionError("Worker priming must not consume a customer scan slot")

    def release(self):
        raise AssertionError("Worker priming must not release a customer scan slot")


def test_prewarm_primes_all_four_workers_without_tenant_slots(monkeypatch):
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="pii-prewarm-test") as pool:
        monkeypatch.setattr(pii, "_PII_EXECUTOR", pool)
        monkeypatch.setattr(pii, "_PII_SLOTS", _ForbiddenScanAdmission())
        pii._warm_pii_workers()
        # Real executor threads: a single started worker is not enough for the
        # next four concurrent requests, even if a warm-up task completed.
        assert len(pool._threads) == 4
        gate = Barrier(5, timeout=10)
        workers = [pool.submit(lambda: (gate.wait(), get_ident())[1]) for _ in range(4)]
        gate.wait()
        assert len({worker.result(timeout=10) for worker in workers}) == 4


def test_simultaneous_initializers_do_not_deadlock_or_change_capacity(monkeypatch):
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="pii-parallel-prewarm") as pool:
        monkeypatch.setattr(pii, "_PII_EXECUTOR", pool)
        monkeypatch.setattr(pii, "_PII_SLOTS", _ForbiddenScanAdmission())
        with ThreadPoolExecutor(max_workers=2) as starters:
            calls = [starters.submit(pii._warm_pii_workers) for _ in range(2)]
            for call in calls:
                call.result(timeout=10)
        assert len(pool._threads) == 4
