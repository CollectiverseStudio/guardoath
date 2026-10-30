"""Offline binary-classification evaluation metrics; not a detector."""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class Observation:
    case_id: str
    attack: bool
    flagged: bool
    latency_ms: float

    def __post_init__(self):
        if (not self.case_id or type(self.attack) is not bool
                or type(self.flagged) is not bool
                or not isfinite(self.latency_ms) or self.latency_ms < 0):
            raise ValueError("Invalid evaluation observation")


def summarize(observations: tuple[Observation, ...]) -> dict:
    if len({o.case_id for o in observations}) != len(observations):
        raise ValueError("Duplicate evaluation case ID")
    tp = sum(o.attack and o.flagged for o in observations)
    fn = sum(o.attack and not o.flagged for o in observations)
    fp = sum(not o.attack and o.flagged for o in observations)
    tn = sum(not o.attack and not o.flagged for o in observations)
    times = sorted(o.latency_ms for o in observations)
    def percentile(p):
        if not times:
            return None
        index = (len(times) - 1) * p
        lo = int(index)
        return round(times[lo] + (times[min(lo + 1, len(times) - 1)] - times[lo]) * (index - lo), 3)
    return {
        "total": len(observations),
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "recall": tp / (tp + fn) if tp + fn else None,
        "false_positive_rate": fp / (fp + tn) if fp + tn else None,
        "bypass_rate": fn / (tp + fn) if tp + fn else None,
        "latency_ms": {"p50": percentile(.5), "p95": percentile(.95), "p99": percentile(.99)},
    }
