"""Red-team scenario harness: all source examples, both positive and benign."""
import json

from guardoath import prompt_attack
from tools.redteam_scenarios import evaluate, scenarios


def test_scenarios_are_stable_unique_and_complete():
    cases = scenarios()
    assert len(cases) == 2 * len(prompt_attack.RULES)
    assert len({case["id"] for case in cases}) == len(cases)
    for rule_id in prompt_attack.RULES:
        assert {case["label"] for case in cases if case["rule_id"] == rule_id} == {
            "adversarial", "benign"}


def test_scenarios_report_confusion_matrix_and_no_sensitive_prompt_content():
    report = evaluate()
    assert report["scenario_count"] == 2 * len(prompt_attack.RULES)
    assert sum(report["counts"].values()) == report["scenario_count"]
    assert report["counts"]["fn"] == 0
    assert report["counts"]["fp"] == 0
    assert report["provider_calls"] == 0
    assert report["failures"] == []
    assert report["recall"] == 1.0
    assert report["false_positive_rate"] == 0.0
    serialized = json.dumps(report)
    for case in scenarios():
        assert case["content"] not in serialized
    assert "not independent attack coverage" in serialized
