"""End-to-end local labeled suite tests using existing saved evaluation fixtures."""
import pytest

from guardoath.labeled_evaluation import run_labeled_suite, compare_labeled_suites


def test_real_local_text_security_suite_requires_explicit_labels():
    from guardoath.evaluate import load_suite
    suite = load_suite("evals/text-security.json")
    # A reviewed label is required for each selected case.
    with pytest.raises(ValueError, match="Missing, duplicate or extra labels"):
        run_labeled_suite(
            suite_path="evals/text-security.json",
            policy_path="policies/text-security-batch.json",
            labels={}, case_ids=("controls.soft_hyphen.input",), dataset_version="1",
            checker_version="local-test", commit_sha="test-sha")
    assert len(suite.cases) > 0


def test_local_suite_with_reviewed_labels_produces_metrics():
    from guardoath.evaluate import load_suite
    suite = load_suite("evals/text-security.json")
    # These are explicit fixture classifications for a local demonstration,
    # not independent real-world attack ground truth.
    selected = ("controls.soft_hyphen.input", "controls.soft_hyphen.output")
    labels = {case_id: True for case_id in selected}
    result = run_labeled_suite(
        suite_path="evals/text-security.json",
        policy_path="policies/text-security-batch.json",
        labels=labels, case_ids=selected, dataset_version="fixture-demo",
        checker_version="local-test", commit_sha="test-sha")
    assert result.metrics["total"] == len(selected)
    assert result.run.dataset_id == suite.suite_id
    assert result.metrics["tp"] + result.metrics["fp"] + result.metrics["tn"] + result.metrics["fn"] == len(selected)


def test_same_labeled_fixture_can_be_compared_without_missing_cases():
    from guardoath.evaluate import load_suite
    suite = load_suite("evals/text-security.json")
    selected = ("controls.soft_hyphen.input", "controls.soft_hyphen.output")
    labels = {case_id: True for case_id in selected}
    kwargs = dict(suite_path="evals/text-security.json",
                  policy_path="policies/text-security-batch.json", labels=labels, case_ids=selected,
                  dataset_version="fixture-demo", checker_version="local-test",
                  commit_sha="test-sha")
    a = run_labeled_suite(**kwargs)
    b = run_labeled_suite(**kwargs)
    comparison = compare_labeled_suites(a, b)
    assert comparison["case_count"] == len(suite.cases)
    assert comparison["recall_delta"] == 0
    assert comparison["newly_missed_attack_ids"] == []
