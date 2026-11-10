"""Real operator CLI boundaries; all keys and references are synthetic."""
from datetime import datetime, timedelta, timezone
import json
import os
import re
from pathlib import Path
import subprocess
import sys

import pytest

from guardoath.models import AdvancedTrustedReferenceRule
from guardoath.trusted_reference import SIGNING_KEY_ENV, reference_is_trusted, sign_reference

KEY = "synthetic-signing-cli-key-for-regression-only-0123456789"
TENANT = "signing-regression"
MARKER = "PRIVATE_SYNTHETIC_REFERENCE_VALUE"
ROOT = Path(__file__).resolve().parents[1]


def draft():
    now = datetime.now(timezone.utc)
    return {
        "rule_id": "signed_vendor", "kind": "invoice_po_vendor",
        "fields": {"invoice_vendor_id": "/vendor"},
        "reference_values": {"approved_vendor_id": "SYNTHETIC_VENDOR_ONLY"},
        "source_id": "synthetic-source", "reference_version": "v1",
        "verified_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "host_signature": "0" * 64,
    }


def run_cli(tmp_path, content, *, output=None):
    source = tmp_path / "unsigned.json"
    source.write_bytes(content if isinstance(content, bytes) else content.encode())
    destination = output or tmp_path / "signed.json"
    return invoke_cli(source, destination), source, destination


def invoke_cli(source, destination, *, tenant=TENANT):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    env[SIGNING_KEY_ENV] = KEY
    result = subprocess.run(
        [sys.executable, "-m", "tools.sign_trusted_reference", "--tenant", tenant,
         "--input", str(source), "--output", str(destination)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=10,
    )
    return result


def test_real_cli_creates_private_tenant_bound_attestation(tmp_path):
    raw = json.dumps(draft())
    result, source, output = run_cli(tmp_path, raw)
    assert result.returncode == 0
    signed = AdvancedTrustedReferenceRule.model_validate_json(output.read_text())
    assert reference_is_trusted(signed, TENANT, key=KEY)
    assert not reference_is_trusted(signed, "another-tenant", key=KEY)
    assert source.read_text() == raw
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600
    assert KEY not in result.stdout + result.stderr
    assert "SYNTHETIC_VENDOR_ONLY" not in result.stdout + result.stderr


@pytest.mark.parametrize("position", ["root", "nested"])
def test_duplicate_json_keys_are_rejected_before_signing(tmp_path, position):
    item = draft()
    raw = json.dumps(item)
    if position == "root":
        raw = '{"source_id":"different-source",' + raw[1:]
    else:
        raw = raw.replace(json.dumps(item["reference_values"]),
                          '{"approved_vendor_id":"FIRST","approved_vendor_id":"SYNTHETIC_VENDOR_ONLY"}')
    result, source, output = run_cli(tmp_path, raw)
    assert result.returncode == 2
    assert not output.exists()
    assert source.read_text() == raw


def test_invalid_private_model_input_does_not_escape_to_diagnostics(tmp_path):
    item = draft()
    item["reference_values"]["approved_vendor_id"] = {"private": MARKER}
    result, _, output = run_cli(tmp_path, json.dumps(item))
    assert MARKER not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert result.returncode == 2
    assert not output.exists()


def test_oversized_input_is_rejected_before_signing(tmp_path):
    from guardoath.json_data import MAX_JSON_BYTES
    raw = json.dumps(draft())
    raw += " " * (MAX_JSON_BYTES + 1 - len(raw.encode()))
    result, _, output = run_cli(tmp_path, raw)
    assert result.returncode == 2
    assert not output.exists()


def test_exact_input_byte_boundary_is_still_accepted(tmp_path):
    from guardoath.json_data import MAX_JSON_BYTES
    raw = json.dumps(draft())
    raw += " " * (MAX_JSON_BYTES - len(raw.encode()))
    result, source, output = run_cli(tmp_path, raw)
    assert result.returncode == 0
    assert source.stat().st_size == MAX_JSON_BYTES
    signed = AdvancedTrustedReferenceRule.model_validate_json(output.read_text())
    assert reference_is_trusted(signed, TENANT, key=KEY)


@pytest.mark.parametrize("raw", [b"\xff", '{"unterminated":', '[]'])
def test_malformed_input_has_safe_exit_and_no_output(tmp_path, raw):
    result, _, output = run_cli(tmp_path, raw)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_existing_private_output_is_neither_overwritten_nor_echoed(tmp_path):
    output = tmp_path / (MARKER + ".json")
    output.write_text("existing private snapshot")
    result, _, _ = run_cli(tmp_path, json.dumps(draft()), output=output)
    assert result.returncode == 2
    assert output.read_text() == "existing private snapshot"
    assert MARKER not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_unsigned_source_is_never_overwritten(tmp_path):
    raw = json.dumps(draft())
    result, source, _ = run_cli(tmp_path, raw, output=tmp_path / "unsigned.json")
    assert result.returncode == 2
    assert source.read_text() == raw


def test_semantically_invalid_reference_is_not_attested(tmp_path):
    item = draft()
    item.update(kind="permit_cap", fields={"reported_emission": "/emission"},
                reference_values={"permit_limit": "NaN"})
    result, _, output = run_cli(tmp_path, json.dumps(item))
    assert result.returncode == 2
    assert not output.exists()


def test_missing_private_input_path_is_not_echoed(tmp_path):
    source = tmp_path / (MARKER + ".json")
    output = tmp_path / "signed.json"
    result = invoke_cli(source, output)
    assert result.returncode == 2
    assert MARKER not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("case", [
    "expired",
    "not_yet_valid",
    "reversed_window",
    "beyond_maximum_lifetime",
])
def test_operator_refuses_unusable_reference_window_without_creating_artifact(tmp_path, case):
    now = datetime.now(timezone.utc)
    item = draft()
    if case == "expired":
        verified, expires = now - timedelta(hours=2), now - timedelta(minutes=1)
    elif case == "not_yet_valid":
        verified, expires = now + timedelta(hours=1), now + timedelta(hours=2)
    elif case == "reversed_window":
        verified, expires = now - timedelta(minutes=1), now - timedelta(hours=2)
    else:
        verified, expires = now - timedelta(minutes=1), now + timedelta(days=367)
    item["verified_at"] = verified.isoformat()
    item["expires_at"] = expires.isoformat()
    result, source, destination = run_cli(tmp_path, json.dumps(item))
    assert result.returncode == 2
    assert "no attestation created" in result.stderr
    assert "Traceback" not in result.stderr
    assert KEY not in result.stdout + result.stderr
    assert "SYNTHETIC_VENDOR_ONLY" not in result.stdout + result.stderr
    assert not destination.exists()
    assert source.exists()


@pytest.mark.parametrize("tenant", [
    "../other-tenant",
    "UPPERCASE",
    "a",
    "a" * 41,
    "valid-id;echo-private",
])
def test_operator_refuses_invalid_tenant_id_without_writing_or_echoing(tmp_path, tenant):
    source = tmp_path / "unsigned.json"
    destination = tmp_path / "signed.json"
    source.write_text(json.dumps(draft()))
    result = invoke_cli(source, destination, tenant=tenant)
    assert result.returncode == 2
    assert not destination.exists()
    # Single-character invalid identifiers can be ordinary letters in
    # argparse's fixed usage text. Check that the supplied identifier does
    # not appear as a standalone value, rather than banning that character.
    transcript = result.stdout + result.stderr
    assert re.search(r"(?<![A-Za-z0-9])" + re.escape(tenant) +
                     r"(?![A-Za-z0-9])", transcript) is None
    assert KEY not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_operator_writes_current_valid_reference_and_rejects_duplicate_destination(tmp_path):
    result, _, destination = run_cli(tmp_path, json.dumps(draft()))
    assert result.returncode == 0
    original = destination.read_bytes()
    assert reference_is_trusted(
        AdvancedTrustedReferenceRule.model_validate_json(original), TENANT, key=KEY)
    result2, _, destination2 = run_cli(tmp_path, json.dumps(draft()))
    assert destination2 == destination
    assert result2.returncode == 2
    assert destination.read_bytes() == original


def test_cli_attestation_runs_through_authenticated_tenant_api_and_engine(tmp_path, monkeypatch):
    """Operator CLI -> persisted tenant -> customer API -> one shared Engine."""
    from fastapi.testclient import TestClient

    from guardoath.app import create_app, demo_engine
    from guardoath.checkers import default_checkers
    from guardoath.hosted import TenantService
    from guardoath.tenant_record import TenantRecord, demo_key, key_hash
    from guardoath.tenant_store import MemoryTenantStore

    result, _, output = run_cli(tmp_path, json.dumps(draft()))
    assert result.returncode == 0
    signed = AdvancedTrustedReferenceRule.model_validate_json(output.read_text())
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    secret = "synthetic-operator-cli-api-secret-01234567890123456789"
    token = demo_key(TENANT, secret)
    record = TenantRecord(
        tenant_id=TENANT, display_name="Synthetic operator reference", demo=True,
        overage="block", monthly_allowance=20, key_hashes=(key_hash(token),),
        input_check_text_security="flag", output_check_text_security="flag",
    )
    tenants = TenantService(MemoryTenantStore((record,)), default_checkers(), secret)
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants)) as api:
        before = api.get("/v1/applications/current", headers=headers)
        assert before.status_code == 200
        revision = before.json()["tenant"]["revision"]
        save = api.post("/v1/tenants/current/checks", headers=headers, json={
            "revision": revision,
            "changes": {
                "advanced_trusted_reference": [signed.model_dump(mode="json")],
                "input_check_cross_field": "block",
                "output_check_cross_field": "flag",
            },
        })
        assert save.status_code == 200, save.text[:150]
        summary = api.get("/v1/applications/current", headers=headers)
        assert summary.status_code == 200
        live = summary.json()["tenant"]
        assert live["revision"] == revision + 1
        assert live["advanced_trusted_reference_status"] == [{
            "rule_id": "signed_vendor", "kind": "invoice_po_vendor",
            "stages": ["input", "output"],
            "snapshot_present": True, "runtime_integrity": "accepted",
        }]
        assert signed.host_signature not in summary.text
        assert "SYNTHETIC_VENDOR_ONLY" not in summary.text
        assert KEY not in summary.text

        for stage, action in (("input", "block"), ("output", "flag")):
            for vendor, expected in (("SYNTHETIC_VENDOR_ONLY", "allow"),
                                     ("OTHER_SYNTHETIC_VENDOR", action)):
                response = api.post("/v1/checks/" + stage, headers=headers, json={
                    "application_id": TENANT, "content_format": "json",
                    "content": json.dumps({"vendor": vendor}),
                })
                assert response.status_code == 200, response.text[:150]
                body = response.json()
                assert body["evaluation_status"] == "complete"
                assert body["effective_action"] == expected
                if vendor != "SYNTHETIC_VENDOR_ONLY":
                    assert "business.signed_vendor" in {f["rule_id"] for f in body["findings"]}
                assert signed.host_signature not in response.text
                assert KEY not in response.text


@pytest.mark.parametrize("signature", ["f" * 64, "1" * 64])
def test_operator_never_silently_resigns_existing_signed_snapshot(tmp_path, signature):
    item = draft()
    item["host_signature"] = signature
    result, source, output = run_cli(tmp_path, json.dumps(item))
    assert result.returncode == 2
    assert source.exists() and not output.exists()
    assert signature not in result.stdout + result.stderr
    assert KEY not in result.stdout + result.stderr
    assert "SYNTHETIC_VENDOR_ONLY" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_operator_refuses_to_resign_a_genuinely_valid_signed_snapshot(tmp_path):
    """Previously valid signed references require a fresh independent unsigned draft."""
    original = AdvancedTrustedReferenceRule.model_validate(draft())
    signed = original.model_copy(update={
        "host_signature": sign_reference(original, TENANT, KEY),
    })
    assert reference_is_trusted(signed, TENANT, key=KEY)
    raw = signed.model_dump_json()
    result, source, output = run_cli(tmp_path, raw)
    assert result.returncode == 2
    assert not output.exists()
    assert source.read_text() == raw
    assert "Unsigned attestation placeholder required" not in result.stderr
    assert signed.host_signature not in result.stdout + result.stderr
    assert KEY not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("failure", ["durability_sync", "open_file_wrapper", "atomic_publish"])
def test_operator_does_not_leave_partial_private_attestation_on_io_failure(
        tmp_path, monkeypatch, capsys, failure):
    """A failed write/sync cannot leave a misleading signed artifact on disk."""
    from tools import sign_trusted_reference as signer

    source = tmp_path / "unsigned.json"
    output = tmp_path / "signed.json"
    original = json.dumps(draft())
    source.write_text(original)
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)

    def unavailable(*_args, **_kwargs):
        raise OSError("PRIVATE_SYNTHETIC_STORAGE_ERROR")

    if failure == "durability_sync":
        monkeypatch.setattr(signer.os, "fsync", unavailable)
    elif failure == "open_file_wrapper":
        monkeypatch.setattr(signer.os, "fdopen", unavailable)
    else:
        monkeypatch.setattr(signer.os, "link", unavailable)

    with pytest.raises(SystemExit) as error:
        signer.main(["--tenant", TENANT, "--input", str(source), "--output", str(output)])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert "Private attestation write failed" in captured.err
    assert "PRIVATE_SYNTHETIC_STORAGE_ERROR" not in captured.out + captured.err
    assert "SYNTHETIC_VENDOR_ONLY" not in captured.out + captured.err
    assert KEY not in captured.out + captured.err
    assert "Traceback" not in captured.err
    assert not output.exists()
    assert not list(tmp_path.glob(".signed.json.*.tmp"))
    assert source.read_text() == original


def test_host_attestation_publishes_only_after_private_temp_is_fully_synced(
        tmp_path, monkeypatch):
    """A reader never observes a partial or unsynced final signed artifact."""
    from tools import sign_trusted_reference as signer

    unsigned = tmp_path / "unsigned.json"
    destination = tmp_path / "signed.json"
    unsigned.write_text(json.dumps(draft()))
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    actual_sync = signer.os.fsync
    observed = []

    def check_before_publish(fd):
        present = destination.exists()
        temporaries = list(tmp_path.glob(".signed.json.*.tmp"))
        assert len(temporaries) == 1
        temp = temporaries[0]
        unsigned_rule = AdvancedTrustedReferenceRule.model_validate_json(temp.read_bytes())
        assert reference_is_trusted(unsigned_rule, TENANT, key=KEY)
        if os.name != "nt":
            assert temp.stat().st_mode & 0o777 == 0o600
        # First fsync belongs to the private file, while a second POSIX
        # fsync must only happen once the full signed file is published.
        assert present == bool(observed)
        observed.append(present)
        return actual_sync(fd)

    monkeypatch.setattr(signer.os, "fsync", check_before_publish)
    assert signer.main([
        "--tenant", TENANT, "--input", str(unsigned), "--output", str(destination),
    ]) == 0
    assert observed == ([False] if os.name == "nt" else [False, True])
    assert destination.exists()
    assert not list(tmp_path.glob(".signed.json.*.tmp"))
    assert reference_is_trusted(
        AdvancedTrustedReferenceRule.model_validate_json(destination.read_bytes()),
        TENANT, key=KEY)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync is unavailable on Windows")
@pytest.mark.parametrize("competing_replacement", [False, True])
def test_directory_sync_failure_revokes_only_our_attestation(
        tmp_path, monkeypatch, capsys, competing_replacement):
    """Never report success without durable directory metadata on POSIX."""
    from tools import sign_trusted_reference as signer

    source = tmp_path / "unsigned.json"
    destination = tmp_path / "signed.json"
    original = json.dumps(draft())
    source.write_text(original)
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    real_sync = signer.os.fsync
    calls = []
    competitor = "PRIVATE_SYNTHETIC_NEW_OUTPUT"

    def fail_after_publication(fd):
        calls.append(fd)
        if len(calls) == 2:
            assert destination.exists()
            if competing_replacement:
                # A different operator's replacement must never be unlinked
                # by the failure handler for this signer.
                destination.unlink()
                destination.write_text(competitor)
            raise OSError("PRIVATE_SYNTHETIC_DIRECTORY_SYNC_ERROR")
        return real_sync(fd)

    monkeypatch.setattr(signer.os, "fsync", fail_after_publication)
    with pytest.raises(SystemExit) as error:
        signer.main([
            "--tenant", TENANT, "--input", str(source), "--output", str(destination),
        ])
    assert error.value.code == 2
    assert len(calls) == 2
    if competing_replacement:
        assert destination.read_text() == competitor
    else:
        assert not destination.exists()
    assert not list(tmp_path.glob(".signed.json.*.tmp"))
    assert source.read_text() == original
    transcript = capsys.readouterr().err
    assert "no confirmed durable artifact" in transcript
    assert "PRIVATE_SYNTHETIC_DIRECTORY_SYNC_ERROR" not in transcript
    assert competitor not in transcript
    assert KEY not in transcript
    assert "Traceback" not in transcript


def test_atomic_attestation_publish_never_overwrites_racing_destination(
        tmp_path, monkeypatch, capsys):
    """Another publisher winning the output name cannot be overwritten/deleted."""
    from tools import sign_trusted_reference as signer

    source = tmp_path / "unsigned.json"
    destination = tmp_path / "signed.json"
    source.write_text(json.dumps(draft()))
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    marker = "PRIVATE_SYNTHETIC_COMPETING_ATTESTATION"

    def competing_writer(_private_file, target):
        Path(target).write_text(marker)
        raise FileExistsError("do not disclose competing private output")

    monkeypatch.setattr(signer.os, "link", competing_writer)
    with pytest.raises(SystemExit) as error:
        signer.main([
            "--tenant", TENANT, "--input", str(source), "--output", str(destination),
        ])
    assert error.value.code == 2
    assert destination.read_text() == marker
    assert not list(tmp_path.glob(".signed.json.*.tmp"))
    captured = capsys.readouterr()
    assert marker not in captured.out + captured.err
    assert KEY not in captured.out + captured.err
    assert "Traceback" not in captured.err



def test_concurrent_operator_attestations_publish_exactly_once(tmp_path, monkeypatch):
    """Two authorized signers racing for one name cannot overwrite one another."""
    from concurrent.futures import ThreadPoolExecutor
    from tools import sign_trusted_reference as signer

    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    output = tmp_path / "signed.json"
    sources = []
    for revision in ("v1", "v2"):
        item = draft()
        item["reference_version"] = revision
        source = tmp_path / ("unsigned-" + revision + ".json")
        source.write_text(json.dumps(item))
        sources.append(source)

    def publish(source):
        try:
            return signer.main([
                "--tenant", TENANT, "--input", str(source),
                "--output", str(output),
            ])
        except SystemExit as failure:
            return failure.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, sources))
    assert sorted(results) == [0, 2]
    assert not list(tmp_path.glob(".signed.json.*.tmp"))
    signed = AdvancedTrustedReferenceRule.model_validate_json(output.read_bytes())
    assert signed.reference_version in ("v1", "v2")
    assert reference_is_trusted(signed, TENANT, key=KEY)
    assert all(source.exists() for source in sources)


@pytest.mark.skipif(os.name == "nt", reason="Symlink privileges vary by Windows environment")
def test_operator_never_follows_an_existing_private_output_symlink(tmp_path):
    """The exclusive output path must never replace or dereference a private target."""
    source = tmp_path / "unsigned.json"
    output = tmp_path / "signed.json"
    existing = tmp_path / "private-original.json"
    source.write_text(json.dumps(draft()))
    sentinel = "PRIVATE_SYNTHETIC_EXISTING_ATTESTATION"
    existing.write_text(sentinel)
    output.symlink_to(existing)
    result = invoke_cli(source, output)
    assert result.returncode == 2
    assert output.is_symlink()
    assert existing.read_text() == sentinel
    assert sentinel not in result.stdout + result.stderr
    assert KEY not in result.stdout + result.stderr
