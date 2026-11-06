"""Operator-only offline attestation for pre-verified FC reference snapshots.

Requires an operator-managed host signing key in GUARDOATH_REFERENCE_SIGNING_KEY
(minimum 32 characters), and a JSON rule with a 64-zero placeholder signature.
Never enter this key on the command line or commit the signed data or signing key
to source control. Signature proves only host approval, not source truth.
"""
import argparse
import json
import os
from pathlib import Path

from guardoath.models import AdvancedTrustedReferenceRule
from guardoath.trusted_reference import SIGNING_KEY_ENV, sign_reference


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="Exact tenant ID")
    parser.add_argument("--input", type=Path, required=True, help="Pre-verified rule JSON")
    parser.add_argument("--output", type=Path, required=True, help="Signed private JSON destination")
    args = parser.parse_args(argv)
    key = os.getenv(SIGNING_KEY_ENV)
    if not key or len(key) < 32:
        parser.error(f"Provide an operator-managed {SIGNING_KEY_ENV} with at least 32 characters")
    if args.input.resolve() == args.output.resolve():
        parser.error("Refusing to overwrite the unsigned source or emit a secret to stdout")
    rule = AdvancedTrustedReferenceRule.model_validate(json.loads(args.input.read_text(encoding="utf-8")))
    signed = rule.model_copy(update={"host_signature": sign_reference(rule, args.tenant, key)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Signed clinical/permit/PO snapshots can be sensitive. Create with
    # owner-only mode, never overwrite an existing file or follow a symlink.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(signed.model_dump(mode="json"), indent=2) + "\n")
    print("Host attestation created; verify and save the resulting tenant rule through the authenticated settings API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
