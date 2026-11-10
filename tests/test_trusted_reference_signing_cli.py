"""Real operator CLI boundaries; all keys and references are synthetic."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from guardoath.models import AdvancedTrustedReferenceRule
from guardoath.trusted_reference import SIGNING_KEY_ENV, reference_is_trusted

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
    assert tenant not in result.stdout + result.stderr
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
