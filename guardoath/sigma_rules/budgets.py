"""Context-local execution budgets for one isolated Sigma event/check.

Nested matcher calls share the outer budget; they cannot reset its deadline or
worker count. This is synchronous API budgeting, not a hosted tenant entitlement.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import math
from time import monotonic


class SigmaEvaluationError(RuntimeError):
    """Evaluation is incomplete and must not be interpreted as a clean event."""


class SigmaRegexTimeout(SigmaEvaluationError):
    """A worker exceeded its per-search execution deadline."""


class SigmaBudgetExceeded(SigmaEvaluationError):
    """An event/check exhausted its shared time or worker-launch budget."""


@dataclass
class _Budget:
    deadline: float
    max_workers: int
    workers: int = 0


_CURRENT = ContextVar('sigma_evaluation_budget', default=None)


def remaining_seconds(*, launch_worker=False):
    budget = _CURRENT.get()
    if budget is None:
        raise RuntimeError('Sigma budget context required')
    remaining = budget.deadline - monotonic()
    if remaining <= 0:
        raise SigmaBudgetExceeded('Sigma evaluation deadline exceeded')
    if launch_worker:
        if budget.workers >= budget.max_workers:
            raise SigmaBudgetExceeded('Sigma regex worker budget exceeded')
        budget.workers += 1
    return remaining


@contextmanager
def evaluation_budget(*, max_workers=4, timeout_seconds=1.0):
    if type(max_workers) is not int or not 1 <= max_workers <= 16:
        raise ValueError('max_workers must be an integer from 1 to 16')
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 5):
        raise ValueError('timeout_seconds must be greater than zero and at most five')
    inherited = _CURRENT.get()
    if inherited is not None:
        remaining_seconds()
        yield inherited
        remaining_seconds()
        return
    token = _CURRENT.set(_Budget(monotonic() + timeout_seconds, max_workers))
    try:
        yield _CURRENT.get()
        remaining_seconds()
    finally:
        _CURRENT.reset(token)
