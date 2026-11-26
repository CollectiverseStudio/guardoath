"""Unchanged-source refund writers and explicitly disabled readers in fresh apps."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("refund_phase,scenario", [
    ("disabled", "disabled"), ("source", "pending"), ("source", "indeterminate")],
    ids=["explicit-disabled-compatibility", "unchanged-source-pending", "unchanged-source-ambiguous"])
def test_mounted_refund_source_gate_and_authenticated_full_refund_recovery(tmp_path, refund_phase, scenario):
    root = Path(__file__).resolve().parents[1]
    environment = {key:value for key,value in os.environ.items()
        if not key.startswith(("GUARDOATH_", "AWS_"))}
    writer = refund_phase == "source"
    environment.update({"PYTHONPATH":str(root) + os.pathsep + str(root / "tests"),
        "GUARDOATH_ADMIN_TEST_DB":str(tmp_path / "refund.sqlite3"),
        "GUARDOATH_ADMIN_TEST_WRITER_PHASE":"source",
        "GUARDOATH_ADMIN_TEST_REFUND_WRITER_PHASE":refund_phase,
        "AWS_EC2_METADATA_DISABLED":"true", "PYTHONDONTWRITEBYTECODE":"1"})
    result = subprocess.run([sys.executable, str(root / "tests/admin_refund_mount_runner.py"), refund_phase, scenario],
        cwd=root, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["phase"] == "source" and evidence["refund_phase"] == refund_phase
    assert evidence["scenario"] == scenario and evidence["writer"] is writer
    assert evidence["status"] == ("verified" if writer else "disabled")
    assert evidence["provider_posts"] == (1 if writer else 0)
    assert evidence["signed_refund_documents"] == (4 if writer else 0)
    assert evidence["tenant_preserved"] is True and evidence["signed_read_receipts"] >= 4
    assert set(evidence["denied"]) == {"customer", "no-mfa", "stale-mfa", "forged"}
