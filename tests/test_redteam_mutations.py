"""Bounded deterministic adversarial mutation evidence contracts."""
import json

import pytest

from tools.redteam_mutations import MAX_CASES, TRANSFORMS, run_mutations
from tools.redteam_scenarios import INDEPENDENT_FIXTURES


def test_mutations_are_deterministic_bounded_and_no_provider_calls():
    report = run_mutations()
    assert report["scenario_count"] == 15 * len(TRANSFORMS)
    assert sum(report["counts"].values()) == report["scenario_count"]
    assert report["provider_calls"] == 0
    assert report == run_mutations()
    assert report["recall"] is not None
    assert report["false_positive_rate"] is not None
    assert set(report["by_transform"]) == set(TRANSFORMS)


def test_mutation_budget_is_enforced():
    report = run_mutations(max_cases=7)
    assert report["scenario_count"] == 7
    for bad in (0, -1, MAX_CASES + 1):
        with pytest.raises(ValueError):
            run_mutations(max_cases=bad)


def test_mutation_reports_never_echo_prompts():
    report = run_mutations()
    serialized = json.dumps(report)
    fixture = json.loads(INDEPENDENT_FIXTURES.read_text(encoding="utf-8"))
    for case in fixture["cases"]:
        assert case["text"] not in serialized
        for transform in TRANSFORMS.values():
            assert transform(case["text"]) not in serialized
    assert all(set(item) == {"scenario_id", "transform", "outcome"} for item in report["misses"])
