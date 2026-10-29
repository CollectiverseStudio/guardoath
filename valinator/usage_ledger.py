"""Bounded, thread-safe in-memory idempotent usage ledger for local testing.

No external billing side effects. Production requires durable transactions.
"""
from threading import Lock

from valinator.billing import UsageEvent


class UsageConflict(ValueError):
    pass


class UsageLedger:
    def __init__(self, max_events: int = 10000):
        if type(max_events) is not int or not 1 <= max_events <= 1_000_000:
            raise ValueError("Invalid ledger capacity")
        self._max_events = max_events
        self._events: dict[str, UsageEvent] = {}
        self._lock = Lock()

    def record(self, event: UsageEvent) -> bool:
        """True for new event, False for exact replay; conflicting replay rejected."""
        with self._lock:
            existing = self._events.get(event.event_id)
            if existing is not None:
                if existing != event:
                    raise UsageConflict("Conflicting usage event ID")
                return False
            if len(self._events) >= self._max_events:
                raise UsageConflict("Usage ledger capacity exceeded")
            self._events[event.event_id] = event
            return True

    def events_for(self, tenant_id: str) -> tuple[UsageEvent, ...]:
        with self._lock:
            return tuple(event for event in self._events.values()
                         if event.tenant_id == tenant_id)
