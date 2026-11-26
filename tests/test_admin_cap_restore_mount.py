"""Reader-first source, disabled compatibility, and isolated future restoration."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("cap_restore_phase,scenario", [
    ("source", "reader"), ("disabled", "reader"),
    ("enabled", "applied"), ("enabled", "indeterminate")], ids=[
    "unchanged-source-reader", "explicit-disabled-compatibility",
    "future-writer-applied", "future-writer-uncertain-then-recovered"])
def test_mounted_cap_restoration_authorization_and_signed_recovery(tmp_path, cap_restore_phase, scenario):
    root = Path(__file__).resolve().parents[1]
    environment = {key:value for key,value in os.environ.items()
        if not key.startswith(("GUARDOATH_", "AWS_"))}
    environment.update({"PYTHONPATH":str(root) + os.pathsep + str(root / "tests"),
        "GUARDOATH_ADMIN_TEST_DB":str(tmp_path / "cap-restore.sqlite3"),
        "GUARDOATH_ADMIN_TEST_WRITER_PHASE":"source",
        "GUARDOATH_ADMIN_TEST_REFUND_WRITER_PHASE":"source",
        "GUARDOATH_ADMIN_TEST_CAP_RESTORE_WRITER_PHASE":cap_restore_phase,
        "AWS_EC2_METADATA_DISABLED":"true", "PYTHONDONTWRITEBYTECODE":"1"})
    result = subprocess.run([sys.executable, str(root / "tests/admin_cap_restore_mount_runner.py"),
        cap_restore_phase, scenario], cwd=root, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    writer = cap_restore_phase == "enabled"
    assert evidence["phase"] == "source" and evidence["cap_restore_phase"] == cap_restore_phase
    assert evidence["scenario"] == scenario and evidence["writer"] is writer
    assert evidence["status"] == ("applied" if writer else "reader")
    assert evidence["provider_reads"] == (2 if writer else 1) and evidence["provider_posts"] == 0
    assert evidence["restore_events"] == (1 if writer else 0)
    assert evidence["later_cap_preserved"] is writer
    assert evidence["preserved_suspension_keys_rules_usage"] is True and evidence["signed_read_receipts"] >= 4
    assert set(evidence["denied"]) == {"customer", "no-mfa", "stale-mfa", "forged"}
