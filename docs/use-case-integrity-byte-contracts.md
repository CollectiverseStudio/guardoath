# Content integrity — exact-byte SHA-256 acceptance and boundaries

**Checker:** `integrity` in `guardoath/data_quality.py`. **Configuration:** server-owned `integrity.sha256`, `integrity.encoding`, `integrity.max_bytes`, selected `integrity` rule. This is a local exact-byte equality check, **not** a digital signature, provenance proof, malware scan, or AI-safety assessment.

## Use cases, acceptance, and benign examples

| Use case | Input | Expected result |
|---|---|---|
| Exact reference bytes | `demo dataset\n` against the hash of those bytes | Complete, no finding, Allow |
| UTF-8 multibyte text | `café` against its actual UTF-8 digest | Complete, no finding |
| Case change | `Demo dataset\n` against `demo dataset\n` | `structure.integrity_digest_mismatch` |
| Line ending changed | CRLF rather than LF | Mismatch finding |
| Final newline missing | Content omits the final LF | Mismatch finding |
| Added BOM or whitespace | BOM prefix or trailing space | Mismatch finding; nothing normalized |
| Strict Base64 binary | Encode `00 FF 10` and hash decoded bytes | Complete, no finding |
| Strict Base64 text | Base64 of approved UTF-8 bytes | Complete, no finding |
| Invalid Base64 | Whitespace, unexpected character, label prefix, excess padding, noncanonical pad bits | Incomplete, `integrity_invalid_encoding`, Block |
| Exact three-byte limit | Three bytes under `max_bytes=3` | Complete, no finding |
| Four bytes when max is three | Raw UTF-8 or decoded Base64 | Incomplete, `integrity_byte_limit`, Block |
| Integrity-only JSON-like text | Duplicate-key JSON-looking content, hash matches exact text | Integrity passes; it **does not parse JSON** |

A completed digest mismatch is configured to **Flag on Input** and **Block on Output** for this test's enforce-mode policy. Incomplete required checking **Blocks** on either stage. Tests assert the original finding ID and never expect any raw content or the configured SHA-256 reference to appear in finding explanations or returned metadata.

## Expected behavior, configuration, and limitations

SHA-256 digests are computed against **exact bytes**, with constant-time digest comparison. `utf8` encodes the text exactly as UTF-8. `base64` requires one fully valid Base64-encoded content string, decodes once, and hashes the decoded bytes. No Unicode normalization, whitespace normalization, JSON canonicalization, implicit Base64-prefix removal, URL fetching or filesystem access is performed. The content format field does not change digest semantics; combine integrity with JSON schema checking if structural validity is required.

The configured digest must be a trusted server-side value, not a string copied from the same untrusted message. The existing tenant/API contract rejects caller-side digest overrides. A matching digest only means the content equals the configured reference; it does not prove who approved that reference, whether the content is safe, or that the model should execute it.

The check has a per-document byte budget: `max_bytes` defaults to 16,384 and can be configured within the accepted 1–262,144 range. A binary payload is submitted as Base64 text; this is not a streamed-file interface. All tests use synthetic inputs.

## Regression evidence and release gate

- New independent test suite: `tests/test_integrity_byte_contracts.py` (44 parametrized executions, covering both Input and Output).
- Existing baseline suite: `tests/test_data_quality.py` and `evals/integrity.json`.
- Existing user instructions: `docs/user-guide.md` and `docs/data-quality-coverage.md`.
- The new test suite and this documentation are committed as *tests and acceptance evidence*, not a runtime change or production deployment. The exact implementation commit must pass 13/13 GitHub Tests jobs plus 3/3 CodeQL and the production release must additionally be approved and followed by 15/15 live checks.

## Canonical Base64 requirement

The integrity decoder rejects alternate representations of identical bytes, including redundant padding (`YWJj====`) and nonzero unused pad bits (`YR==` for `a`). Alphabet validation alone is insufficient on some supported Python versions. GuardOath now re-encodes decoded bytes and requires exact canonical Base64 equality. Noncanonical content yields `integrity_invalid_encoding` and an incomplete required check; raw content is never echoed. Valid standard Base64 still passes, without changing hash logic, decoded byte budget or fail-closed action.
