"""Offline labeled effectiveness workflow using existing GuardOath local evals.

This is an explicit Python API: labels are supplied by a trusted evaluator,
not inferred from whether an expectation matched. No network calls or storage.
"""
import asyncio
from dataclasses import dataclass
from collections.abc import Mapping

from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.evaluate import evaluate, load_suite
from guardoath.evaluation_adapter import observations_from_report
from guardoath.effectiveness import summarize
from guardoath.evaluation_comparison import EvaluationRun, compare_runs


@dataclass(frozen=True)
class LabeledRun:
    run: EvaluationRun
    metrics: dict


def run_labeled_suite(*, suite_path: str, policy_path: str,
                      labels: Mapping[str, bool], dataset_version: str,
                      checker_version: str, commit_sha: str,
                      enable_pii: bool = False) -> LabeledRun:
    """Run local-only checks and reject unreviewed or incomplete evidence."""
    suite = load_suite(suite_path)
    engine = Engine(load_policies(policy_path),
                    default_checkers(enable_pii=enable_pii))
    report = asyncio.run(evaluate(suite, engine))
    observations = observations_from_report(report, labels)
    policy_versions = {case.request.policy_version for case in suite.cases}
    if len(policy_versions) != 1:
        raise ValueError("Mixed policy versions require separate reports")
    run = EvaluationRun(
        dataset_id=suite.suite_id, dataset_version=dataset_version,
        policy_version=next(iter(policy_versions)),
        checker_version=checker_version, commit_sha=commit_sha,
        observations=observations)
    return LabeledRun(run=run, metrics=summarize(observations))


def compare_labeled_suites(baseline: LabeledRun, candidate: LabeledRun) -> dict:
    return compare_runs(baseline.run, candidate.run)
