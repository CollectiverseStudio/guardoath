"""Offline versioned evaluation comparisons; no provider or model calls."""
import pytest

from guardoath.effectiveness import Observation
from guardoath.evaluation_comparison import EvaluationRun, compare_runs


def run(observations, *, version="v1", dataset="holdout", dataset_version="1"):
    return EvaluationRun(dataset, dataset_version, version, "checker-1",
                         "abc123", tuple(observations))


def test_paired_comparison_identifies_attack_regression_and_benign_false_positive():
    old = run((Observation("attack-a", True, True, 10),
               Observation("attack-b", True, True, 10),
               Observation("benign-a", False, False, 10)))
    new = run((Observation("benign-a", False, True, 12),
               Observation("attack-b", True, True, 12),
               Observation("attack-a", True, False, 12)), version="v2")
    result = compare_runs(old, new)
    assert result["recall_delta"] == -.5
    assert result["false_positive_rate_delta"] == 1.0
    assert result["newly_missed_attack_ids"] == ["attack-a"]
    assert result["newly_flagged_benign_ids"] == ["benign-a"]
    assert result["candidate"]["policy_version"] == "v2"


@pytest.mark.parametrize("candidate", [
    run((Observation("a", True, True, 1),), dataset_version="2"),
    run((Observation("b", True, True, 1),)),
    run((Observation("a", False, True, 1),)),
])
def test_different_dataset_missing_cases_or_changed_truth_rejected(candidate):
    baseline = run((Observation("a", True, True, 1),))
    with pytest.raises(ValueError):
        compare_runs(baseline, candidate)


def test_empty_and_single_class_comparison_has_unknown_rates():
    result = compare_runs(run(()), run((), version="v2"))
    assert result["recall_delta"] is None
    assert result["false_positive_rate_delta"] is None
    only_benign = (Observation("b", False, False, 1),)
    result = compare_runs(run(only_benign), run(only_benign, version="v2"))
    assert result["recall_delta"] is None
    assert result["false_positive_rate_delta"] == 0


def test_invalid_provenance_and_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="provenance"):
        run((Observation("a", True, True, 1),), dataset="")
    with pytest.raises(ValueError, match="Duplicate"):
        run((Observation("a", True, True, 1),
             Observation("a", False, False, 2)))
