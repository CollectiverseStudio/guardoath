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

def test_independent_challenges_measure_real_misses_without_claiming_success():
    from tools.redteam_scenarios import evaluate_independent
    report = evaluate_independent()
    assert report["suite"] == "independent_challenge_v1"
    assert report["scenario_count"] == 15
    assert sum(report["counts"].values()) == 15
    assert set(report["family_counts"]) == {
        "direct_override", "indirect_injection", "evasion", "exfiltration",
        "multi_turn", "role_spoof"}
    assert report["provider_calls"] == 0
    assert report["recall"] is not None
    assert report["false_positive_rate"] is not None
    assert all(set(item) == {"scenario_id", "family", "outcome"} for item in report["failures"])
    serialized = json.dumps(report)
    from tools.redteam_scenarios import INDEPENDENT_FIXTURES
    fixture = json.loads(INDEPENDENT_FIXTURES.read_text(encoding="utf-8"))
    assert all(item["text"] not in serialized for item in fixture["cases"])

def test_independent_expected_rules_are_not_satisfied_by_unrelated_matches():
    from tools.redteam_scenarios import evaluate_independent, INDEPENDENT_FIXTURES
    from guardoath import prompt_attack
    report = evaluate_independent()
    manifest = json.loads(INDEPENDENT_FIXTURES.read_text(encoding="utf-8"))
    required = [case for case in manifest["cases"] if "expected_rule_id" in case]
    assert len(required) >= 4
    assert report["expected_rule_misses"] == []
    for case in required:
        assert case["expected_rule_id"] in prompt_attack.inspect(case["text"])
    assert all(set(miss) == {"scenario_id", "required_rule_id"}
               for miss in report["expected_rule_misses"])
