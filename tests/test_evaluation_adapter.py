"""Offline adapter tests: passing regression assertions are not attack labels."""
from copy import deepcopy
import pytest

from guardoath.effectiveness import summarize
from guardoath.evaluation_adapter import observations_from_report


def report():
    return {
        "format_version": "1", "execution_scope": "local_engine",
        "cases": [
            {"case_id": "attack", "matched": True, "evaluation_status": "complete",
             "simulated": False, "checker_statuses": {"text_security": "flagged"},
             "finding_rule_ids": ["text_security.hidden_controls"], "latency_ms": 2},
            {"case_id": "benign", "matched": True, "evaluation_status": "complete",
             "simulated": False, "checker_statuses": {"text_security": "passed"},
             "finding_rule_ids": [], "latency_ms": 3},
        ],
    }


def test_reviewed_labels_and_findings_produce_correct_metrics():
    observations = observations_from_report(report(), {"attack": True, "benign": False})
    result = summarize(observations)
    assert (result["tp"], result["tn"], result["fp"], result["fn"]) == (1, 1, 0, 0)


@pytest.mark.parametrize("labels", [
    {"attack": True}, {"attack": True, "benign": False, "extra": True},
    {"attack": 1, "benign": False},
])
def test_missing_extra_or_nonboolean_labels_rejected(labels):
    with pytest.raises(ValueError):
        observations_from_report(report(), labels)


@pytest.mark.parametrize("change", [
    ("matched", False), ("evaluation_status", "incomplete"),
    ("simulated", True), ("checker_statuses", {"text_security": "error"}),
    ("checker_statuses", {"text_security": "skipped"}),
    ("finding_rule_ids", None), ("latency_ms", float("nan")),
])
def test_unscorable_or_incomplete_outcomes_rejected(change):
    data = deepcopy(report())
    data["cases"][0][change[0]] = change[1]
    with pytest.raises((ValueError, TypeError)):
        observations_from_report(data, {"attack": True, "benign": False})


def test_duplicate_ids_and_unsupported_scope_rejected():
    data = report()
    data["cases"][1]["case_id"] = "attack"
    with pytest.raises(ValueError):
        observations_from_report(data, {"attack": True})
    data = report()
    data["execution_scope"] = "remote"
    with pytest.raises(ValueError):
        observations_from_report(data, {"attack": True, "benign": False})


def test_benign_finding_is_false_positive_not_success():
    data = report()
    data["cases"][1]["finding_rule_ids"] = ["text_security.hidden_controls"]
    result = summarize(observations_from_report(
        data, {"attack": True, "benign": False}))
    assert result["fp"] == 1
    assert result["false_positive_rate"] == 1
