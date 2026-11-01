"""Isolated bounded event evaluation; the host must select/route rules first."""
from collections.abc import Mapping, Sequence
import math

from . import ImportedRule
from .budgets import evaluation_budget, remaining_seconds
from .matcher import UnsupportedSigmaRule


def evaluate_event(rules, event, *, max_workers=4, timeout_seconds=1.0):
    """Return all matching findings, or raise; never return partial clean results.

    The caller must authenticate, enforce tenant entitlements, route the original
    Windows logsource, and handle SigmaEvaluationError as incomplete/failed work.
    These source-only results are event signals, not AI prompt safety decisions.
    """
    if (not isinstance(rules, Sequence) or isinstance(rules, (str, bytes))
            or not 1 <= len(rules) <= 128 or any(not isinstance(r, ImportedRule) for r in rules)):
        raise UnsupportedSigmaRule('one to 128 imported rules required')
    if (not isinstance(event, Mapping) or len(event) > 256
            or any(not isinstance(key, str) for key in event)):
        raise UnsupportedSigmaRule('event must have at most 256 string field names')
    for value in event.values():
        if isinstance(value, str):
            if len(value) > 32_000:
                raise UnsupportedSigmaRule('event field exceeds supported bounds')
        elif type(value) not in (int, float, bool, type(None)):
            raise UnsupportedSigmaRule('event fields must be JSON scalars')
        elif type(value) is float and not math.isfinite(value):
            raise UnsupportedSigmaRule('event fields must be finite JSON scalars')
    with evaluation_budget(max_workers=max_workers, timeout_seconds=timeout_seconds):
        findings = []
        for rule in rules:
            remaining_seconds()
            if rule.matches(event):
                findings.append(rule.finding())
        remaining_seconds()
        return findings
