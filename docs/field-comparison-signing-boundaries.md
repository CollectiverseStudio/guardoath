# Host-reference signing and value boundaries

FC-044/045/047/049 use host-signed, tenant-bound reference snapshots. A valid
signature proves host approval of the exact configuration, not external source
truth. Snapshot values must also satisfy the existing declared type and resource
contracts before the reference can be trusted.

## Invalid signed values fail closed

The shared reference-value validator checks positive bounded dose factors,
finite nonnegative permit limits, explicit-offset previous-administration time,
canonical bounded positive interval minutes and supported vendor identifiers.
It uses the same numeric, time and identifier rules as runtime comparisons.

A signed `permit_limit` of `"NaN"`, a negative limit, invalid dose factor,
malformed administration time/interval or invalid vendor identifier now takes
the existing untrusted-reference path: evaluation is incomplete and Block on
both stages, including observe mode and failure-action Flag. Every selected
rule keeps its original finding ID. An explicitly disabled rule stays Off.
Valid references keep the existing comparison and per-stage action semantics.

Existing malformed signed snapshots require operator correction and a new
attestation after source verification. No values are clamped, substituted,
automatically re-signed or silently migrated. Canonical HMAC construction,
tenant binding, signature verification and expiry checks are unchanged.

## Operator signing input and output

```sh
python -m tools.sign_trusted_reference --tenant TENANT --input approved-rule.json --output private-signed-rule.json
```

The operator-managed signing key remains in `GUARDOATH_REFERENCE_SIGNING_KEY`,
never a command-line argument or committed reference fixture. Source verification
is the operator's responsibility; this tool does not retrieve or fabricate a
clinical, regulatory or purchase-order authority.

The CLI reads at most 262,145 bytes and accepts at most 262,144 input bytes,
including whitespace. The shared strict JSON decoder rejects duplicate keys at
any level, invalid UTF-8, nonfinite numeric tokens and structural limits before
model validation. Declared reference values must pass the same semantic validator
used by runtime trust checks.

Invalid inputs and file errors produce a fixed diagnostic and exit 2, without
printing private reference values, private paths, signing keys or tracebacks.
Invalid input creates no signed output. Successful output is created exclusively
with owner-only permissions; existing files and the unsigned source are never
overwritten. A failed write can leave a private incomplete file, which must be
discarded rather than treated as an attestation.

## Regression evidence

Before repair, real subprocess tests reproduced accepted root/nested duplicate
keys, accepted oversized input and private-value traceback disclosure: four
failures and one valid control. A separate real Engine reproduction considered
an HMAC-valid `"NaN"` permit limit trusted and returned complete/effective Allow
under observe; 18 invalid-reference stage cases failed and two Off controls
passed before the runtime fix.

`tests/test_trusted_reference_signing_cli.py` covers actual CLI creation, tenant
binding, byte-boundary acceptance/rejection, duplicate and malformed input,
sanitized model/file errors and non-overwrite behavior.
`tests/test_trusted_reference_value_validity.py` covers all four existing
reference kinds, both stages, mandatory incomplete/Block and preserved Off.
All keys, source labels and reference values in these tests are synthetic.

This is code/contract evidence, not clinical effectiveness, external provenance
verification, PII latency acceptance or production deployment. Publication and
the exact implementation's full Tests/CodeQL jobs are required for CI acceptance.
