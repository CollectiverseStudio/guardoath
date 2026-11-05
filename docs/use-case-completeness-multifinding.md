# Required-section completeness — input/output rule acceptance

**Existing checker:** `guardoath/completeness.py::completeness_check` (version 2). **Existing policy:** `policies/completeness.json`, with an independently tested `mode=enforce` copy for the regression suite. The check evaluates literal labeled-text section **presence, duplication and non-whitespace character counts**. It does not evaluate accuracy, meaning, authorization or safety.

## Use cases, rules and acceptance

| Rule or contract | Synthetic example | Expected result |
|---|---|---|
| `completeness.request_summary`, Input | `Summary: A valid request.` | Complete, Allow |
| `completeness.request_summary`, Input | `Details: Request.` | Missing-section finding, effective Flag |
| `completeness.request_summary`, Input | Two `Summary:` sections | Repeated-section finding, effective Flag |
| `completeness.response_summary`, Output | `Next steps: Contact support.` with no Summary | Missing-section finding, effective Block |
| `completeness.next_steps`, Output | `Summary: Done.` with no Next steps | Missing-section finding, effective Block |
| `completeness.next_steps`, Output | `Next steps: Hi` | Too-short finding, effective Block |
| `completeness.next_steps`, Output | `Next steps: A B C D` | Four non-whitespace characters; Block |
| `completeness.next_steps`, Output | Two `Next steps:` sections | Repeated-section finding, effective Block |
| Both output rules | `Details: unrelated text` | Both finding IDs returned, no first-finding short circuit |
| Literal-label normalization | `SUMMARY:` and `next STEPS:` | Accepted; labels are case-insensitive |
| Multiline and CRLF | Section contents on following lines / CRLF | Accepted if unique and minimum content present |
| Unknown label boundary | `Next steps: Hi\nOther: Long text` | Other section cannot make Next steps reach its minimum |
| Wrong format | JSON object supplied to text-only required-section checker | Incomplete, skipped required check, fail-closed Block |

**Stage ownership:** Input requires only `Summary` (`request_summary`). Output requires `Summary` (`response_summary`) plus `Next steps` (`next_steps`, minimum five non-whitespace characters). The same label intentionally appears in different stages. With enforcement enabled, complete Input findings are flagged, complete Output findings block, and required checker noncompletion is governed by the default fail-closed Block action on either stage.

## What is not assessed

No semantics, factuality, quality, safety, source authenticity, legal validity, consent or API tool authorization is inferred from section labels. Unknown label lines end the prior section and cannot be used to pad an insufficient required section. Duplicate labeled sections are ambiguous, so a convenient occurrence must not be chosen. Findings must contain rule IDs and generic explanations, not submitted body text.

## Acceptance and evidence

- New targeted tests: `tests/test_completeness_multirule_regression.py` (**17 synthetic test executions**) verifying independent stages, benign variants, all applicable finding IDs, extra label boundaries, and wrong-format fail-closed behavior.
- Established tests: `tests/test_completeness.py` and existing completeness evaluation fixtures.
- User guide: `docs/user-guide.md`, Required information/completeness section.

The new tests do not change the checker, tenant record, engine or published policy. Passing a focused test does not prove full release CI or production deployment; the exact latest SHA must pass all required 13 Tests jobs and three CodeQL jobs, followed by separately authorized AWS deployment and 15 live checks.
