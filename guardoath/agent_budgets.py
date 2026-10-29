"""Trusted in-process agent step/depth budgets; not a distributed execution sandbox."""
from dataclasses import dataclass
from threading import Lock


class BudgetExceeded(Exception):
    """A configured per-run agent budget was exceeded."""


@dataclass(frozen=True)
class AgentLimits:
    max_steps: int = 100
    max_depth: int = 8
    max_calls: int = 50

    def __post_init__(self):
        if not 1 <= self.max_steps <= 100_000:
            raise ValueError("Invalid step budget")
        if not 0 <= self.max_depth <= 1_000:
            raise ValueError("Invalid depth budget")
        if not 1 <= self.max_calls <= 100_000:
            raise ValueError("Invalid call budget")


class AgentBudget:
    """Reserve work atomically before dispatch. Never refunds completed reservations."""

    def __init__(self, limits: AgentLimits):
        self.limits = limits
        self._lock = Lock()
        self._steps = 0
        self._calls = 0

    def reserve(self, *, depth: int, steps: int = 1, calls: int = 0) -> None:
        if (type(depth) is not int or type(steps) is not int or type(calls) is not int
                or depth < 0 or steps < 1 or calls < 0):
            raise ValueError("Invalid reservation")
        with self._lock:
            if (depth > self.limits.max_depth
                    or self._steps + steps > self.limits.max_steps
                    or self._calls + calls > self.limits.max_calls):
                raise BudgetExceeded("Agent execution budget exceeded")
            self._steps += steps
            self._calls += calls

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"steps": self._steps, "calls": self._calls}
