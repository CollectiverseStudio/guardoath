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
    # Signed clinical/permit/PO snapshots can be sensitive. Create with
    # owner-only mode, never overwrite an existing file or follow a symlink.
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(signed.model_dump(mode="json"), indent=2) + "\n")
    except OSError:
        parser.error("Cannot create private attestation output; require a new writable destination")
    print("Host attestation created; verify and save the resulting tenant rule through the authenticated settings API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
