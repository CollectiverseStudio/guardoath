"""Unit tests for offline labeled effectiveness accounting."""
import pytest

from guardoath.effectiveness import Observation, summarize


def test_confusion_matrix_and_rates():
    report = summarize((
        Observation("attack-found", True, True, 10),
        Observation("attack-missed", True, False, 20),
        Observation("benign-flagged", False, True, 30),
        Observation("benign-allowed", False, False, 40),
    ))
    assert {key: report[key] for key in ("tp", "fn", "fp", "tn")} == {
        "tp": 1, "fn": 1, "fp": 1, "tn": 1}
    assert report["recall"] == .5
    assert report["false_positive_rate"] == .5
    assert report["bypass_rate"] == .5
    assert report["latency_ms"]["p50"] == 25
    assert report["latency_ms"]["p95"] == 38.5


def test_empty_and_single_class_do_not_fabricate_rates():
    empty = summarize(())
    assert empty["recall"] is None
    assert empty["false_positive_rate"] is None
    assert empty["latency_ms"]["p99"] is None
    only_benign = summarize((Observation("benign", False, False, 0),))
    assert only_benign["recall"] is None
    assert only_benign["false_positive_rate"] == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_latency_rejected(value):
    with pytest.raises(ValueError):
        Observation("case", True, False, value)


def test_duplicate_case_ids_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        summarize((Observation("same", True, True, 1),
                   Observation("same", False, False, 2)))
