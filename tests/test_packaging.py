"""The built wheel must carry the data files the installed package reads (2026-11-03).

The CI test jobs install the checkout in editable mode, where every file is present whether or not pyproject.toml
declares it. These tests check the declarations against the files, and the release-wheel CI job builds the real
wheel and runs the Sigma distribution and installed-package checks on it."""
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DATA = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]


def declared(package):
    folder = ROOT.joinpath(*package.split("."))
    return {path.relative_to(folder).as_posix() for pattern in PACKAGE_DATA[package] for path in folder.glob(pattern)}


def test_every_sigma_asset_the_release_check_requires_is_declared():
    folder = ROOT / "guardoath" / "sigma_rules"
    snapshots = {p.relative_to(folder).as_posix() for p in folder.glob("upstream/batch_*/*.yml")}
    manifests = {p.name for p in folder.glob("upstream_batch_*.json")}
    assert snapshots and manifests
    required = snapshots | manifests | {"LICENSE.Detection.Rules.md"}
    assert required <= declared("guardoath.sigma_rules")


def test_sigma_review_candidates_are_not_shipped():
    # guardoath/sigma_rules/candidates/ holds rules still under review; the release check rejects unindexed
    # snapshots, so they must stay out of the wheel.
    assert not any(name.startswith("candidates/") for name in declared("guardoath.sigma_rules"))


def test_the_ci_workflow_builds_and_checks_the_real_wheel():
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    for text in ["release-wheel:", "python -m pip wheel --no-deps --wheel-dir dist .",
                 "python -m guardoath.sigma_rules.verify_distribution dist/guardoath-*.whl",
                 "pip install --no-index --no-deps dist/guardoath-*.whl",
                 "-I -m guardoath.sigma_rules.verify_installation --expected-root"]:
        assert text in workflow, text
