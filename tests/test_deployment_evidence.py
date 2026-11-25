"""Real completion and private receipt boundaries, without contacting AWS."""
from copy import deepcopy
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from deploy.aws.deployment_evidence import (
    configuration_evidence, receipt_destination, validate_receipt,
    verify_completion, write_receipt,
)

HASH = base64.b64encode(bytes(range(32))).decode()


def configuration(**changes):
    result = {
        "FunctionName": "guardoath-site",
        "FunctionArn": "arn:aws:lambda:us-east-1:839000214942:function:guardoath-site",
        "Runtime": "python3.13", "Handler": "index.handler", "Role": "synthetic-role",
        "Timeout": 15, "MemorySize": 512, "Version": "$LATEST", "CodeSha256": HASH,
        "RevisionId": "upload-revision", "LastUpdateStatus": "InProgress",
        "LastModified": "2026-11-24T12:00:00.000+0000",
        "Environment": {"Variables": {"SECRET": "private-provider-body"}},
    }
    result.update(changes)
    return result


def completion(**changes):
    acknowledged = configuration_evidence(configuration())
    value = {"acknowledged": acknowledged,
             "observed": configuration(RevisionId="completed-state", LastUpdateStatus="Successful"),
             "expected_code_sha256": HASH,
             "original_configuration_sha256": acknowledged["configuration_sha256"]}
    value.update(changes)
    return value


def receipt():
    return {
        "format": "guardoath.catalog-deployment-receipt.v1", "invocation_id": "1" * 64,
        "source_sha": "a" * 40, "code_sha256": HASH, "backup_code_sha256": HASH,
        "backup_source_sha": "b" * 40, "original_revision_id": "original-revision",
        "upload_revision_id": "upload-revision", "verified_revision_id": "completed-state",
        "last_modified": "2026-11-24T12:00:00.000+0000", "configuration_sha256": "2" * 64,
        "account_id": "839000214942", "region": "us-east-1", "function_name": "guardoath-site",
        "stack_name": "guardoath-site", "distribution_id": "E3ECSRDHRP80CT", "invalidation_id": "I123456",
        "observed_at": 1795564800, "automatic_rollback_owned": False, "last_update_status": "Successful",
        "release_source_parity_verified": True, "dashboard_ui_matches_local_source": True,
        "expected_source_sha": "a" * 40, "live_source_sha": "a" * 40,
        "verified_asset_count": 13, "expected_asset_count": 13, "production_live_acceptance": False,
    }


def test_asynchronous_revision_is_verified_without_adopting_rollback_authority():
    result = verify_completion(completion())
    assert result["revision_id"] == "completed-state"
    assert result["upload_revision_id"] == "upload-revision"
    assert result["automatic_rollback_owned"] is False
    same = completion(observed=configuration(LastUpdateStatus="Successful"))
    assert verify_completion(same)["automatic_rollback_owned"] is True
    assert "private-provider-body" not in json.dumps(result)


@pytest.mark.parametrize("change", [
    {"LastModified": "2026-11-24T12:00:01.000+0000"},
    {"CodeSha256": base64.b64encode(b"x" * 32).decode()},
    {"LastUpdateStatus": "Failed"}, {"LastUpdateStatus": "InProgress"},
    {"Environment": {"Variables": {"SECRET": "changed-private-body"}}},
    {"Role": "another-role"}, {"Timeout": 16}, {"MemorySize": 1024},
    {"FunctionArn": "arn:aws:lambda:us-east-1:839000214942:function:another-function"},
    {"FutureSetting": {"unexpected": True}}, {"Environment": {"Error": {"Message": "private-provider-body"}}},
])
def test_other_update_or_unverifiable_configuration_does_not_complete(change):
    value = completion()
    value["observed"].update(change)
    with pytest.raises(ValueError, match="Deployment evidence refused"):
        verify_completion(value)


def test_configuration_fingerprint_is_order_independent_and_keeps_unknown_settings():
    original = configuration()
    reordered = dict(reversed(list(original.items())))
    assert configuration_evidence(original) == configuration_evidence(reordered)
    changed = configuration(FutureSetting=True)
    assert configuration_evidence(original)["configuration_sha256"] != configuration_evidence(changed)["configuration_sha256"]
    value = completion()
    value["acknowledged"]["configuration_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        verify_completion(value)


def test_raw_provider_json_preserves_acknowledged_timestamp_and_wrapped_configuration():
    raw = json.dumps(configuration())
    evidence = configuration_evidence(raw)
    assert evidence["last_modified"] == "2026-11-24T12:00:00.000+0000"
    assert configuration_evidence(json.dumps({"Configuration": configuration(), "Code": {"Location": "private-url"}})) == evidence
    value = completion(observed=json.dumps(configuration(RevisionId="completed-state", LastUpdateStatus="Successful")))
    assert verify_completion(value)["last_modified"] == evidence["last_modified"]


@pytest.mark.parametrize("key,value", [
    ("invocation_id", "short"), ("source_sha", "b" * 40), ("live_source_sha", "b" * 40),
    ("account_id", "111111111111"), ("region", "us-west-2"), ("function_name", "other"),
    ("stack_name", "other"), ("automatic_rollback_owned", True), ("production_live_acceptance", True),
    ("verified_asset_count", 12), ("expected_asset_count", 14), ("observed_at", True),
    ("last_update_status", "InProgress"), ("release_source_parity_verified", False),
    ("dashboard_ui_matches_local_source", False), ("code_sha256", "bad"),
])
def test_receipt_cannot_claim_wrong_source_resource_partial_parity_or_owned_revision(key, value):
    item = receipt()
    item[key] = value
    with pytest.raises(ValueError):
        validate_receipt(item)


@pytest.mark.skipif(os.name != "posix", reason="Private standalone worker requires verified POSIX directory")
def test_receipt_is_private_exclusive_and_never_written_to_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir(mode=0o700)
    private = tmp_path / "operation"
    private.mkdir(mode=0o700)
    path = private / "deployment.json"
    value = {"receipt": receipt(), "path": str(path), "checkout": str(checkout)}
    assert write_receipt(value) == {"receipt_written": True}
    assert json.loads(path.read_text()) == receipt()
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        write_receipt(value)
    with pytest.raises(ValueError):
        receipt_destination(str(checkout / "result.json"), str(checkout))
    path.unlink()
    path.symlink_to(private / "missing")
    with pytest.raises(ValueError):
        write_receipt(value)
    private.chmod(0o755)
    with pytest.raises(ValueError):
        receipt_destination(str(private / "other.json"), str(checkout))


def test_private_provider_input_never_leaks_on_cli_failure():
    command = [sys.executable, "deploy/aws/deployment_evidence.py", "inspect"]
    result = subprocess.run(command, input=json.dumps({"Environment": "private-provider-body"}),
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert result.stdout.strip() == "Deployment evidence refused"
    assert "private-provider-body" not in result.stdout + result.stderr
    duplicate = subprocess.run(command, input='{"Environment":"private-provider-body","Environment":{}}',
                               capture_output=True, text=True, check=False)
    assert duplicate.returncode == 1
    assert "private-provider-body" not in duplicate.stdout + duplicate.stderr
