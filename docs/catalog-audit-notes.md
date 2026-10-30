# GuardOath catalog audit coordination notes

## Batch 1 — non-privacy UC003, UC005, UC006, UC007

The manifest now records statuses and exact code/test/eval evidence for these four cases. `partial` means a bounded portion of the requested capability exists; `not_implemented` means the requested end-to-end capability is not present, even if adjacent safety components exist. No checker or eval suite was changed.

| ID | Status | Evidence summary |
|---|---|---|
| UC003 | partial | `guardoath/text_security.py`; `test_hidden_control_signals`, `test_legitimate_text_and_unlabeled_data`; `controls.zero_width_space.input`, `controls.bidi_override.input`, `scope.emoji_joiner.input` in text-security suite |
| UC005 | not_implemented | `guardoath/text_security.py`; `test_legitimate_text_and_unlabeled_data`; `scope.plain_attack_not_detected.input` documents a local semantic coverage gap |
| UC006 | not_implemented | `guardoath/tool_calls.py`; `test_plain_code_is_not_treated_as_validated_action_safety`, `test_the_entire_batch_cannot_invoke_tools_or_use_network`; dedicated eval ID: unknown / none identified |
| UC007 | partial | `guardoath/ingress.py`, `guardoath/budgets.py`, `guardoath/json_data.py`; `test_http_rejects_oversize_before_request_validation`, `test_parser_limits`, `test_token_bucket_refills_caps_and_rounds_retry`, `test_real_api_concurrency_limits_and_cancellation_cleanup`; dedicated eval ID: unknown / none identified |

## Out-of-scope changes requested

- `guardoath/coverage_report.py` currently recognizes a fixed status vocabulary. It should accept `not_implemented` and `supported` (or the manifest should use the original vocabulary consistently). This file is not Atlas-owned. Owner should reconcile the status vocabulary before the coverage reporter is treated as authoritative.
- Verify whether `UC006` warrants a dedicated negative eval case in a newly created suite, rather than treating proposed-tool-call checks as a sandbox control.
- This audit does not change privacy cases reserved for the later coordinated commit.

## Release discipline

All updates are additive to catalog metadata and tests. No tests or existing eval cases were deleted, skipped, or weakened. CI is the verification source; no local clone was used.

## CI reconciliation after first batch

Tests #175 exposed a status-vocabulary incompatibility with `guardoath/coverage_report.py` and `tests/test_coverage_report.py`. Catalog entries UC005/UC006 now retain canonical `review_required` status and record `audit_classification: not_implemented` with negative evidence. This avoids editing the unowned reporter or weakening its tests. UC003/UC007 remain `partial`.

Independent baseline failures on `test_configuration.py::test_pii_checker_loads_only_its_declared_dependencies` (Cython modules already imported) and three dashboard assertions were also observed; those files are reserved and were not changed. Owner of commit B should investigate. Audit work should not claim green CI while these failures remain.

## Batch 2 — non-privacy UC018–UC020

All three retain canonical `review_required` and receive `audit_classification: not_implemented`, with concrete negative evidence rather than a false verified claim:

- UC018 misinformation: `guardoath/evidence.py`; `tests/test_evidence.py::test_valid_citation_does_not_establish_claim_entailment`, `test_no_network_or_reference_lookup`. Dedicated misinformation eval ID: unknown.
- UC019 plagiarism: `guardoath/evidence.py`; `tests/test_evidence.py::test_no_network_or_reference_lookup`. Dedicated plagiarism eval ID: unknown.
- UC020 defamation: `guardoath/evidence.py`; `tests/test_evidence.py::test_valid_citation_does_not_establish_claim_entailment`. Dedicated defamation eval ID: unknown.

The manifest audit test now requires evidence for seven audited entries. No existing tests/evals or engine behavior were changed. The coverage reporter remains compatible with canonical statuses.

CI currently has failures in files owned by commit B (privacy dependency isolation and dashboard) and cannot be claimed green; see prior notes. 

## Batch 3 — non-privacy UC024, UC042, UC044, UC050

- UC024 `review_required → partial`: `guardoath/secrets_check.py`; `test_supported_candidates`, `test_benign_and_incomplete_lookalikes`, `test_no_verification_or_network` in `tests/test_secrets_check.py`. Matching saved eval case ID: unknown. Pattern credentials do not imply all corporate secrets are detected.
- UC042 `review_required → partial`: `guardoath/completeness.py`; `test_labeled_sections_pass`, `test_missing_short_or_ambiguous_sections_flag`, `test_labels_are_literal_not_semantic` in `tests/test_completeness.py`. Matching saved eval case ID: unknown. Literal labels do not certify legal adequacy.
- UC044 retains `review_required`, `audit_classification=not_implemented`: `guardoath/tool_calls.py`; `tests/test_tool_calls.py::test_plain_code_is_not_treated_as_validated_action_safety` and `test_no_calls_is_not_an_execution_or_authorization_claim`; actions suite `valid.lookup.input`, `allowlist.unknown.input` show adjacent action validation, **not** code syntax verification.
- UC050 `review_required → partial`: `guardoath/evidence.py`; `test_each_citation_relationship`, `test_valid_citation_does_not_establish_claim_entailment`, `test_no_network_or_reference_lookup` in `tests/test_evidence.py`; evidence suite `valid.repeated-marker.input`, `citation.missing-entry.input`, `source.unknown-marker.input`. No academic source authenticity check.

The audit evidence test now covers eleven entries. Added test functions: 0; added saved eval cases: 0. No checker changes. New dedicated cases for partial coverage are still needed.
