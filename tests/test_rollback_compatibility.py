"""A deploy must have a real, digest-bound rollback that understands its writes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from guardoath.state_contracts import release_metadata

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_rollback",ROOT / "deploy/aws/verify_rollback.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
SHA = "a" * 40
OLD = "b" * 40
CONTRACT = "subscription-cap-provenance-v1"


def archive(path, metadata=None, *, raw=None, duplicate=False):
    with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_DEFLATED) as bundle:
        # Must never be executed or extracted by compatibility inspection.
        bundle.writestr("index.py","raise RuntimeError('Archive inspection executed code')\n")
        if metadata is not None or raw is not None:
            content = raw if raw is not None else json.dumps(metadata).encode()
            bundle.writestr(gate.MANIFEST,content)
            if duplicate:
                with pytest.warns(UserWarning,match="Duplicate name"):
                    bundle.writestr(gate.MANIFEST,content)
    return path


def metadata(sha=SHA, *, support=(CONTRACT,), writes=(CONTRACT,)):
    return {"source_sha":sha,"release_contract_version":1,
            "supported_state_contracts":list(support),"introduced_state_contracts":list(writes)}


def verify(candidate,backup):
    return gate.verify_rollback(candidate,backup,expected_sha=SHA,backup_code_sha256=gate.archive_digest(backup))


def test_legacy_can_accept_reader_baseline_but_never_new_provenance_writers(tmp_path):
    old = archive(tmp_path / "old.zip",{"source_sha":OLD})
    baseline = archive(tmp_path / "baseline.zip",metadata(writes=()))
    writer = archive(tmp_path / "writer.zip",metadata())
    assert verify(baseline,old)["status"] == "compatible"
    with pytest.raises(ValueError,match="compatibility baseline"):
        verify(writer,old)
    assert verify(writer,baseline)["status"] == "compatible"


def test_current_baseline_has_actual_build_metadata_compatible_with_legacy_backup(tmp_path):
    spec = importlib.util.spec_from_file_location("build_lambda",ROOT / "deploy/aws/build_lambda.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    actual = build.release_metadata(SHA)
    assert actual == release_metadata(SHA)
    assert actual["introduced_state_contracts"] == [] and CONTRACT in actual["supported_state_contracts"]
    old = archive(tmp_path / "old.zip")
    candidate = archive(tmp_path / "candidate.zip",actual)
    assert verify(candidate,old)["status"] == "compatible"
    writer = archive(tmp_path / "writer.zip",metadata(OLD))
    assert verify(candidate,writer)["status"] == "compatible"


def test_enabling_actual_writer_code_also_declares_incompatible_new_writes(tmp_path,monkeypatch):
    from guardoath import state_contracts
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_SUBSCRIPTION_STATE",True)
    actual = release_metadata(SHA)
    assert actual["introduced_state_contracts"] == [CONTRACT]
    old = archive(tmp_path / "old.zip")
    candidate = archive(tmp_path / "candidate.zip",actual)
    with pytest.raises(ValueError,match="Rollback image cannot preserve"):
        verify(candidate,old)


def test_support_floor_cannot_be_forgotten_after_a_compatible_release(tmp_path):
    current = archive(tmp_path / "current.zip",metadata(OLD,writes=()))
    incompatible = archive(tmp_path / "new.zip",metadata(support=(),writes=()))
    with pytest.raises(ValueError,match="drops deployed"):
        verify(incompatible,current)


@pytest.mark.parametrize("changes", [
    {"source_sha":"A" * 40}, {"source_sha":None}, {"release_contract_version":True},
    {"release_contract_version":2}, {"supported_state_contracts":[CONTRACT,CONTRACT]},
    {"introduced_state_contracts":[CONTRACT,CONTRACT]}, {"supported_state_contracts":CONTRACT},
    {"supported_state_contracts":[False]}, {"supported_state_contracts":[]},
    {"introduced_state_contracts":["unsafe/name"]}, {"unapproved_override":True},
])
def test_malformed_explicit_contract_is_never_interpreted_as_legacy(tmp_path,changes):
    bad = archive(tmp_path / "bad.zip",{**metadata(OLD),**changes})
    candidate = archive(tmp_path / "new.zip",metadata(writes=()))
    with pytest.raises(ValueError): verify(candidate,bad)


@pytest.mark.parametrize("raw", [
    b"not-json", b"[]", b'{"source_sha":"'+SHA.encode()+b'","source_sha":"'+OLD.encode()+b'"}',
    b'{"source_sha":"'+SHA.encode()+b'","release_contract_version":NaN}',
    b"x" * (gate.MAX_MANIFEST_BYTES + 1),
])
def test_invalid_or_ambiguous_manifest_never_allows_deployment(tmp_path,raw):
    candidate = archive(tmp_path / "new.zip",metadata())
    backup = archive(tmp_path / "bad.zip",raw=raw)
    with pytest.raises(ValueError): verify(candidate,backup)


def test_missing_duplicate_incomplete_or_mismatched_candidate_refused(tmp_path):
    backup = archive(tmp_path / "backup.zip",metadata(OLD,writes=()))
    for index,candidate in enumerate((None,{"source_sha":SHA},metadata("c" * 40))):
        path = archive(tmp_path / f"candidate-{index}.zip",candidate)
        with pytest.raises(ValueError): verify(path,backup)
    with pytest.raises(ValueError,match="duplicate"):
        verify(archive(tmp_path / "duplicate.zip",metadata(),duplicate=True),backup)


def test_backup_digest_binds_verification_and_later_restore_to_same_archive(tmp_path):
    backup = archive(tmp_path / "backup.zip",metadata(OLD,writes=()))
    candidate = archive(tmp_path / "candidate.zip",metadata())
    digest = gate.archive_digest(backup)
    assert gate.verify_rollback(candidate,backup,expected_sha=SHA,backup_code_sha256=digest)["status"] == "compatible"
    assert gate.assert_backup_digest(backup,digest) == digest
    archive(backup,{"source_sha":OLD})
    with pytest.raises(ValueError,match="CodeSha256"):
        gate.assert_backup_digest(backup,digest)
    with pytest.raises(ValueError,match="CodeSha256"):
        gate.verify_rollback(candidate,backup,expected_sha=SHA,backup_code_sha256=digest)


@pytest.mark.parametrize("digest", [None,"","bad", "a" * 64, True])
def test_invalid_deployed_digest_is_not_a_backup_attestation(tmp_path,digest):
    backup = archive(tmp_path / "backup.zip",metadata(OLD,writes=()))
    with pytest.raises(ValueError): gate.assert_backup_digest(backup,digest)


def test_real_cli_refuses_unsafe_pair_and_can_reverify_only_accepted_backup(tmp_path):
    backup = archive(tmp_path / "backup.zip",metadata(OLD,writes=()))
    candidate = archive(tmp_path / "candidate.zip",metadata())
    common = [sys.executable,str(ROOT / "deploy/aws/verify_rollback.py"),"--backup",str(backup),
              "--backup-code-sha256",gate.archive_digest(backup)]
    result = subprocess.run([*common,"--candidate",str(candidate),"--expected-sha",SHA],capture_output=True,text=True)
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "compatible"
    result = subprocess.run([*common,"--verify-backup-only"],capture_output=True,text=True)
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "backup_digest_verified"
    result = subprocess.run([*common,"--candidate",str(candidate),"--expected-sha",OLD],capture_output=True,text=True)
    assert result.returncode == 1 and "differs from accepted exact SHA" in result.stdout
    assert not (tmp_path / "index.py").exists()
