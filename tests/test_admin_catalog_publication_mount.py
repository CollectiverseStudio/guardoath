"""Real mounted owner routes with actual signed identity and reopened state."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("phase", ["0", "1"], ids=["compatibility_baseline", "retained_writer"])
def test_mounted_catalog_publication_preserves_auth_audit_and_tenants(tmp_path, phase):
    root = Path(__file__).resolve().parents[1]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("GUARDOATH_", "AWS_"))}
    environment.update({"PYTHONPATH": str(root) + os.pathsep + str(root / "tests"),
        "GUARDOATH_ADMIN_TEST_DB": str(tmp_path / "mounted.sqlite3"),
        "GUARDOATH_ADMIN_TEST_WRITER_PHASE": phase, "AWS_EC2_METADATA_DISABLED": "true",
        "PYTHONDONTWRITEBYTECODE": "1"})
    result = subprocess.run([sys.executable, str(root / "tests/admin_catalog_publication_mount_runner.py")],
        cwd=root, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["phase"] == phase
    assert evidence["signed_read_receipts"] == (19 if phase == "1" else 7)
    assert set(evidence["denied"]) == {"customer", "no-mfa", "stale-mfa", "forged"}
    assert evidence["request_transactions"] == evidence["intent_documents"] == int(phase)
    assert evidence["tenants_unchanged"] and evidence["catalog_unchanged"]
    assert evidence["remote_published"] is evidence["ci_verified"] is evidence["deployed"] is False
