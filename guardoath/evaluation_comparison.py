"""Compare paired, versioned offline evaluation runs without hiding missing cases.

Only evaluates already labeled outcomes; never invokes models or providers.
"""
from dataclasses import dataclass

from guardoath.effectiveness import Observation, summarize


@dataclass(frozen=True)
class EvaluationRun:
    dataset_id: str
    dataset_version: str
    policy_version: str
    checker_version: str
    commit_sha: str
    observations: tuple[Observation, ...]

    def __post_init__(self):
        fields = (self.dataset_id, self.dataset_version, self.policy_version,
                  self.checker_version, self.commit_sha)
        if any(not isinstance(value, str) or not value.strip() for value in fields):
            raise ValueError("Evaluation provenance is required")
        summarize(self.observations)  # rejects duplicate case IDs


def compare_runs(baseline: EvaluationRun, candidate: EvaluationRun) -> dict:
    """Require exactly matching case IDs and ground-truth labels before comparing."""
    if (baseline.dataset_id, baseline.dataset_version) != (
            candidate.dataset_id, candidate.dataset_version):
        raise ValueError("Different datasets cannot be compared")
    old = {item.case_id: item for item in baseline.observations}
    new = {item.case_id: item for item in candidate.observations}
    if old.keys() != new.keys():
        raise ValueError("Evaluation cases differ between runs")
    if any(old[key].attack != new[key].attack for key in old):
        raise ValueError("Ground truth changed between runs")
    a, b = summarize(baseline.observations), summarize(candidate.observations)
    def delta(key):
        if a[key] is None or b[key] is None:
            return None
        return b[key] - a[key]
    return {
        "dataset_id": baseline.dataset_id,
        "dataset_version": baseline.dataset_version,
        "case_count": len(old),
        "baseline": {"policy_version": baseline.policy_version,
                     "checker_version": baseline.checker_version,
                     "commit_sha": baseline.commit_sha, "metrics": a},
        "candidate": {"policy_version": candidate.policy_version,
                      "checker_version": candidate.checker_version,
                      "commit_sha": candidate.commit_sha, "metrics": b},
        "recall_delta": delta("recall"),
        "false_positive_rate_delta": delta("false_positive_rate"),
        "bypass_rate_delta": delta("bypass_rate"),
        "newly_missed_attack_ids": sorted(
            key for key in old if old[key].attack
            and old[key].flagged and not new[key].flagged),
        "newly_flagged_benign_ids": sorted(
            key for key in old if not old[key].attack
            and not old[key].flagged and new[key].flagged),
    }
