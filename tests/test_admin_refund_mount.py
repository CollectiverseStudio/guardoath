"""Source-default readers and explicit future refund writer in a fresh app process."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("scenario", ["source", "disabled", "pending", "indeterminate"])
def test_mounted_refund_source_gate_and_authenticated_full_refund_recovery(tmp_path, scenario):
    root = Path(__file__).resolve().parents[1]
    environment = {key:value for key,value in os.environ.items()
        if not key.startswith(("GUARDOATH_", "AWS_"))}
    writer = scenario in ("pending", "indeterminate")
    environment.update({"PYTHONPATH":str(root) + os.pathsep + str(root / "tests"),
        "GUARDOATH_ADMIN_TEST_DB":str(tmp_path / "refund.sqlite3"),
        "GUARDOATH_ADMIN_TEST_WRITER_PHASE":"source",
        "GUARDOATH_ADMIN_TEST_REFUND_WRITER_PHASE":"enabled" if writer else scenario,
        "AWS_EC2_METADATA_DISABLED":"true", "PYTHONDONTWRITEBYTECODE":"1"})
    result = subprocess.run([sys.executable, str(root / "tests/admin_refund_mount_runner.py"), scenario],
        cwd=root, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["scenario"] == scenario and evidence["writer"] is writer
    assert evidence["status"] == ("verified" if writer else "disabled")
    assert evidence["provider_posts"] == (1 if writer else 0)
    assert evidence["signed_refund_documents"] == (4 if writer else 0)
    assert evidence["tenant_preserved"] is True and evidence["signed_read_receipts"] >= 4
    assert set(evidence["denied"]) == {"customer", "no-mfa", "stale-mfa", "forged"}
