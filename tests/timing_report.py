"""Where the wall time of one call went: garbage-collection pauses, live threads and total time (2026-11-04).

Several checks run with a 500 ms budget. A check of a short string takes well under 1 ms, yet CI has reported
it unfinished: checker_timeout at 510-573 ms (tests/test_secrets_batch.py, Python 3.14) and an unfinished secrets
check on Python 3.11 (tests/test_secrets_check.py, Tests run 41850888154). Tests that hit this print the report
from timed() with the checker statuses in their assertion message, so a failure says why. Budgets are not raised.
"""
import gc
import threading
from time import perf_counter


def timed(call):
    pauses, started = [], {}

    def watch(phase, info):
        if phase == "start":
            started["at"] = perf_counter()
        elif "at" in started:
            pauses.append((info.get("generation"), round((perf_counter() - started.pop("at")) * 1000, 1)))

    gc.callbacks.append(watch)
    began = perf_counter()
    try:
        result = call()
    finally:
        gc.callbacks.remove(watch)
    return result, {"wall_ms": round((perf_counter() - began) * 1000, 1), "gc_pauses_ms": pauses,
                    "threads": sorted(t.name for t in threading.enumerate())}


def statuses(result):
    """Each checker's status, error code and latency, for an assertion message."""
    return [(c.checker_id, c.status, c.error_code, c.latency_ms) for c in result.checker_results]
