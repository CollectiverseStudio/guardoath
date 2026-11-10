"""Operator-only offline attestation for pre-verified FC reference snapshots.

Requires an operator-managed host signing key in GUARDOATH_REFERENCE_SIGNING_KEY
(minimum 32 characters), and a JSON rule with a 64-zero placeholder signature.
Never enter this key on the command line or commit the signed data or signing key
to source control. Signature proves only host approval, not source truth.
"""
import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from guardoath.json_data import MAX_JSON_BYTES, parse_json
from guardoath.models import AdvancedTrustedReferenceRule
from guardoath.tenant_record import TENANT_ID_PATTERN
from guardoath.trusted_reference import (SIGNING_KEY_ENV, reference_is_trusted,
                                        reference_values_are_valid, sign_reference)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="Exact tenant ID")
    parser.add_argument("--input", type=Path, required=True, help="Pre-verified rule JSON")
    parser.add_argument("--output", type=Path, required=True, help="Signed private JSON destination")
    args = parser.parse_args(argv)
    # Do not issue valid-looking attestations for a tenant identifier that the
    # authoritative tenant record can never accept. Do not echo submitted IDs.
    if not re.fullmatch(TENANT_ID_PATTERN, args.tenant):
        parser.error("Invalid tenant identifier for reference attestation")
    key = os.getenv(SIGNING_KEY_ENV)
    if not key or len(key) < 32:
        parser.error(f"Provide an operator-managed {SIGNING_KEY_ENV} with at least 32 characters")
    try:
        same_path = args.input.resolve() == args.output.resolve()
    except (OSError, RuntimeError):
        parser.error("Cannot resolve reference input or output path")
    if same_path:
        parser.error("Refusing to overwrite the unsigned source or emit a secret to stdout")
    try:
        # Bound the actual read, including whitespace, before decoding or model
        # validation. Use the same duplicate-key/structure rules as admission.
        with args.input.open("rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("Reference input byte limit")
        rule = AdvancedTrustedReferenceRule.model_validate(parse_json(raw.decode("utf-8")))
        if not reference_values_are_valid(rule):
            raise ValueError("Invalid declared reference value")
        # Never silently re-sign an already attested snapshot. The operator
        # must independently reverify the source and submit a fresh unsigned
        # draft with the documented placeholder, rather than laundering an
        # expired, cross-tenant or otherwise untrusted signature.
        if rule.host_signature != "0" * 64:
            raise ValueError("Unsigned attestation placeholder required")
    except (OSError, UnicodeError, ValueError):
        # ValidationError includes submitted values; never print its text or
        # an input path from an OS error into operator diagnostic logs.
        parser.error("Invalid reference input; require bounded UTF-8 JSON with unique keys and valid rule fields")
    signed = rule.model_copy(update={"host_signature": sign_reference(rule, args.tenant, key)})
    # A structurally valid reference can still be not-yet-valid, expired or
    # beyond the maximum authorized lifetime. Such a snapshot would always
    # fail closed at runtime. Refuse to create a misleading operator artifact.
    if not reference_is_trusted(signed, args.tenant, key=key):
        parser.error("Reference validity window is not currently acceptable; no attestation created")
    # Serialize before touching storage. Publishing directly at the destination
    # exposes partial signed JSON while it is being written, even if later
    # removed on a caught exception (or left behind after process termination).
    # Write a private temporary file in the destination directory, sync the
    # full contents, and hard-link it into place atomically. os.link refuses
    # existing destinations, including symlinks: do not use os.replace().
    try:
        contents = (json.dumps(signed.model_dump(mode="json"), indent=2) + "\n").encode("utf-8")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix="." + args.output.name + ".", suffix=".tmp",
            dir=args.output.parent)
    except (OSError, ValueError):
        parser.error("Cannot create private attestation output; require a new writable destination")
    temporary = Path(temporary_name)
    published = False
    try:
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            # fdopen may fail before accepting responsibility for the descriptor.
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        # The output name does not exist until the complete private file has
        # passed fsync. Hard-link publication is same-filesystem and exclusive.
        # No signed bytes are ever streamed directly through the public name.
        os.link(temporary, args.output)
        published = True
        # File fsync makes the bytes durable; a hard link alone does not
        # guarantee that the new destination directory entry survives a
        # sudden power loss. On POSIX, sync the parent directory before
        # reporting an approved attestation. Windows retains the existing
        # atomic-publication guarantee but cannot use POSIX directory fsync.
        if os.name != "nt":
            directory_fd = os.open(
                args.output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        if published:
            # A failed directory sync means persistence was NOT established.
            # Revoke only our own completed hard link, never another process's
            # file that may have replaced this name. A severe storage failure
            # can also prevent cleanup: operators must inspect the directory.
            try:
                expected = os.stat(temporary, follow_symlinks=False)
                current = os.stat(args.output, follow_symlinks=False)
                if (expected.st_dev, expected.st_ino) == (current.st_dev, current.st_ino):
                    args.output.unlink()
            except OSError:
                pass
        parser.error("Private attestation write failed; no confirmed durable artifact")
    finally:
        try:
            temporary.unlink()
        except OSError:
            # A process/storage failure may leave a private mode-0600 temporary
            # file. It is NOT a valid published attestation; operators must
            # keep this directory private and clean orphaned .tmp files.
            pass
    print("Host attestation created; verify and save the resulting tenant rule through the authenticated settings API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
