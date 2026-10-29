# Sensitive data in labeled encoded payloads

Version 0.23.0. `encoded_pii` is an optional real local checker registered with
the existing PII dependency. It scans **one explicitly labeled encoding layer**
with the same offline identifier recognizers as `pii`. It is not a general
obfuscation detector, decryption engine or compliance certification.

## Recognized input and reuse

Supported labels are ASCII, case-insensitive `base64:` and `hex:`, followed by an
unbroken strict Base64/byte-pair hex token decoding to UTF-8. The checker scans
plain-text content or decoded string values/keys throughout bounded outer JSON.
No files, URLs or network services are read. Unlabeled, URL-safe/percent/binary,
compressed, encrypted and MIME-wrapped payloads are not covered. A passed check
cannot certify that all opaque data has been found.

The decoded text is inspected by the existing local PII adapter: email,
credit-card candidates/checksums, selected U.S. SSN candidates, the privacy pattern
types that are on by default (see the user guide), and, when enabled by the policy,
labeled phone, birth date, medical-record, health-plan and bank-account candidates. Ownership, issuance, actual identities and comprehensive
PHI are still not verified. These findings use the corresponding `pii.encoded_`
prefix, such as `pii.encoded_email_address` or
`pii.encoded_medical_record_number`. No decoded/encoded values appear in findings.

Decoded content beginning with an object/array/double quote is treated as JSON.
That preserves nested keys, escapes and supported field hints. Malformed
structured content is incomplete rather than downgraded into an ordinary text
pass. Other decoded UTF-8 is scanned as text. Explicit labels found inside the
decoded text or its JSON strings/keys produce an incomplete nested-payload result;
no recursive decoding occurs. An unlabeled second encoding layer is outside this
coverage and may produce no identifier finding; the suite demonstrates this gap.
Request context is not scanned or sent anywhere.

## Limits and incomplete results

| Code | Meaning |
| --- | --- |
| encoded_pii_budget_exceeded | More than 16 labeled payloads, token over 4,096 characters, or total token characters over 8,192 |
| encoded_pii_invalid_encoding | Empty, malformed or non-UTF-8 labeled payload |
| encoded_pii_invalid_decoded_json | Decoded structured content violates strict JSON/parser bounds |
| encoded_pii_nested_payload | Another explicit encoding label remains after the single supported decode |
| encoded_pii_invalid_json | Outer JSON could not be scanned |
| encoded_pii_scan_incomplete | Underlying identifier scan failed, exceeded a field/deadline limit or did not return a real completed local result |

Counts and token budgets are shared across the whole submitted document,
including JSON keys and values. The PII adapter's 64 labeled-field attempt limit
and 0.2-second scan deadline apply separately to each decoded payload. The engine
also enforces the policy deadline across the entire encoded checker. Calls are
sequential, not fan-out. Cancellation cannot forcibly stop an already running
local scanner thread; existing thread/deadline limitations still apply.

Any unfinished payload discards this checker's partial findings and returns an
error, making required coverage incomplete. It never reports a completed clean
scan of the prefix. Base `pii` findings are separate and may still be visible.
Malformed input is not proof of sensitive content; its scan simply did not finish.

## Policies, redaction and routing

The normal conversation profile now selects `encoded_pii` alongside raw PII,
secrets and formatting checks. The existing general formatting checker can still
flag benign encoding for review. Focused `privacy-encoded-v1` selects raw and
encoded PII only, with labeled fields enabled; benign encoding may pass there.
Legacy focused profiles keep their previous configured coverage.

Sample policies in `policies/encoded-privacy-batch.json` include observe/enforce
and raw-redaction variants. Findings recommend input flag/output block in the
ordinary sample. Redaction variants block encoded findings on either stage.
Observe effective action stays allow, while the dashboard holds findings and
incomplete results. Missing optional PII dependencies leave required checks
unavailable/incomplete. No provider/model download is introduced.

Encoded content is **never rewritten, decoded into an API transformation, or
re-encoded for release**. Raw PII redaction is a separate operation. If raw PII is
redacted but encoded identifiers remain, all-check revalidation withholds the
transformation. For encoded-only identifiers the raw PII rule can say not_needed;
that refers only to raw redaction, and the encoded finding still blocks release.
Applications must inspect the complete decision/findings, not that one status.
An explicitly registered external checker remains gated when this local check
flags/errors, even if its individual finding action is configured allow.

## Complete group batch

```powershell
py -3.13 start_dashboard.py --evaluate --suite encoded-privacy
py -3.13 start_dashboard.py --evaluate --suite all
```

Or use the existing prepared environment:

```bash
python -m guardoath.evaluate --pii --cases evals/encoded-privacy.json --policies policies/encoded-privacy-batch.json
python -m pytest tests/test_encoded_pii.py -q
```

The 50 definitions run on both stages: **100 expectations**, including 28
intentional incomplete assessments. Coverage includes all eight identifier
families, both encodings, decoded JSON/hints, benign/unlabeled scope gaps,
shared budgets, nested/malformed data, partial-result suppression and raw
redaction interactions. Additional unit/API tests verify safe responses,
external gating and no network access. Eight independent dashboard examples per
editor demonstrate common paths. Synthetic regressions are not a reviewed
identifier-accuracy benchmark or proof of complete anonymization.
