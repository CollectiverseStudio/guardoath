"""Boot actual shared registries after real reviewed forward/inverse source writes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_real_app_catalog_source_release_and_monotonic_reversal_preserve_tenants(tmp_path):
    root = Path(__file__).resolve().parents[1]
    original_history = (root / "guardoath/catalog_metadata_releases.json").read_bytes()
    package = tmp_path / "package"
    package.mkdir()
    shutil.copytree(root / "guardoath", package / "guardoath",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(root / "policies", package / "policies")
    support = package / "acceptance"
    support.mkdir()
    for name in ("catalog_source_roundtrip_runner.py", "admin_test_support.py"):
        shutil.copy2(root / "tests" / name, support / name)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("GUARDOATH_", "AWS_"))}
    environment.update({"PYTHONPATH": str(package) + os.pathsep + str(support),
                        "AWS_EC2_METADATA_DISABLED": "true", "PYTHONDONTWRITEBYTECODE": "1"})
    database = tmp_path / "state.sqlite3"

    def execute(*args):
        result = subprocess.run([sys.executable, *map(str, args)], cwd=package,
                                env=environment, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def inspect(name, next_title=None):
        report, plan = tmp_path / (name + ".json"), tmp_path / (name + "-plan.json")
        args = [support / "catalog_source_roundtrip_runner.py", "--database", database,
                "--report", report]
        if name == "baseline":
            args.append("--initialize-store")
        if next_title is not None:
            args.extend(["--plan", plan, "--next-title", next_title])
        execute(*args)
        return json.loads(report.read_text()), plan

    def publish(plan, version, *, reverse=False):
        arguments = ["-m", "guardoath.catalog_source_release", "--plan", plan,
                     "--write", "--confirm-registry-version", version]
        if reverse:
            arguments.append("--revert-last")
        output = execute(*arguments)
        assert "- written - NOT approved or deployed" in output
        assert not list((package / "guardoath").glob(".catalog-release-*"))
        assert not (package / "guardoath/.catalog_metadata_releases.json.write.lock").exists()

    title = "Reviewed force-push guidance"
    baseline, forward_plan = inspect("baseline", title)
    original_title = baseline["rules"]["agent.git_force"]["title"]
    if original_title == title:
        title = "Alternate reviewed force-push guidance"
    assert json.loads(forward_plan.read_text())["changed_metadata"]["title"]["after"] == title
    first_version = int(baseline["registry_version"]) + 1
    publish(forward_plan, str(first_version))
    candidate, inverse_plan = inspect("candidate", original_title)
    assert candidate["registry_version"] == str(first_version)
    assert candidate["rules"]["agent.git_force"]["title"] == title
    normalized_rules = deepcopy(candidate["rules"])
    normalized_rules["agent.git_force"]["title"] = original_title
    assert normalized_rules == baseline["rules"]
    publish(inverse_plan, str(first_version + 1), reverse=True)
    restored, _ = inspect("restored")
    assert restored["registry_version"] == str(first_version + 1)
    assert restored["rules"] == baseline["rules"]
    assert candidate["tenant_rows"] == restored["tenant_rows"] == baseline["tenant_rows"]
    assert candidate["outcomes"] == restored["outcomes"] == baseline["outcomes"]

    for name in ("admin", "customer"):
        for state in (candidate, restored):
            normalized = deepcopy(state[name])
            normalized["registry_version"] = baseline[name]["registry_version"]
            if name == "admin":
                rules = [rule for category in normalized["categories"] for check in category["checks"] for rule in check["rules"]]
            else:
                rules = [rule for domain in normalized["domains"] for check in domain["controls"] for rule in check["rules"]]
            next(rule for rule in rules if rule["rule_id"] == "agent.git_force")["title"] = original_title
            assert normalized == baseline[name]

    old_count = len(baseline["history"]["releases"])
    assert len(candidate["history"]["releases"]) == old_count + 1
    assert len(restored["history"]["releases"]) == old_count + 2
    forward, inverse = restored["history"]["releases"][-2:]
    assert forward["review_id"] != inverse["review_id"] and forward["draft_id"] != inverse["draft_id"]
    assert inverse["changes"] == {field: {"before": delta["after"], "after": delta["before"]}
                                  for field, delta in forward["changes"].items()}
    assert (root / "guardoath/catalog_metadata_releases.json").read_bytes() == original_history
