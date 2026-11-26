"""Actual source-default owner financial routes with independently reopened state."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("phase,scenario", [("0", "compatibility"),
    ("source", "verified"), ("source", "indeterminate")],
    ids=["explicit-disabled-compatibility", "activated-source-confirmed", "activated-source-ambiguous"])
def test_mounted_financial_controls_require_owner_binding_and_signed_recovery(tmp_path, phase, scenario):
    root = Path(__file__).resolve().parents[1]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("GUARDOATH_", "AWS_"))}
    environment.update({"PYTHONPATH": str(root) + os.pathsep + str(root / "tests"),
        "GUARDOATH_ADMIN_TEST_DB": str(tmp_path / "financial.sqlite3"),
        "GUARDOATH_ADMIN_TEST_WRITER_PHASE": phase, "AWS_EC2_METADATA_DISABLED": "true",
        "PYTHONDONTWRITEBYTECODE": "1"})
    result = subprocess.run([sys.executable, str(root / "tests/admin_financial_mount_runner.py"), scenario],
        cwd=root, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["phase"] == phase
    assert set(evidence["denied"]) == {"customer", "no-mfa", "stale-mfa", "forged"}
    assert evidence["status"] == ("disabled" if phase == "0" else scenario)
    assert evidence["provider_posts"] == (0 if phase == "0" else 1)
    assert evidence["financial_documents"] == (0 if phase == "0" else 3)
    assert evidence["mutation_receipts"] == (0 if phase == "0" else 3)
    assert evidence["tenant_preserved"] is True
    if phase == "source":
        assert evidence["reconciliation_recovered"] is True and evidence["saved_cap"] == 4000
