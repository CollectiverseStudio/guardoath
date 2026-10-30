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


## Agent handoff — prepared UC051–100 audit and regression package (2026-10-29)

**Status:** 11 use cases are committed in the manifest; an additional **21 UC051–100 entries are drafted locally, not merged**. This section documents the work for another agent. It does not claim that draft tests passed or that the draft has been committed.

**Repository baseline when drafted:** `main` commit `48ee5a5f16afc0ab798b0fc10bafa1adca15bf85`. The prepared local artifact is `guardoath_catalog_audit_work_package_v2.zip` (held in the originating conversation, **not** in this repository). The other agent must request the ZIP or its constituent files if not available. Do not assume the files exist on disk or in GitHub.

### Prepared package contents

| File inside ZIP | Purpose | Status |
|---|---|---|
| `README.md` | Integration steps, limitations, verification and ownership | Draft |
| `uc051_100_audit_draft.json` | Evidence-backed proposed `partial` classifications for 21 use cases | Draft |
| `test_catalog_partial_boundaries.py` | 4 new parametrized functions, **36 scenarios**, UC068/079/087/092 | Python source compiled; pytest not run |
| `test_catalog_partial_contracts.py` | 5 new parametrized functions, **58 scenarios**, UC074/078/080/085/092 | Python source compiled; pytest not run |
| `apply_catalog_audit.py` | Local integration helper, checks module/test/eval references before updating only the manifest | Python source compiled; not run on repository checkout |

**Totals:** 9 new test functions and 94 parametrized scenarios, not yet added to the repository or executed with pytest. The draft cites 26 existing test-function references (references may repeat across cases). No new checkers or provider integrations were created.

**Draft case IDs (exact):** UC053, UC054, UC062, UC068, UC072, UC074, UC075, UC076, UC077, UC078, UC079, UC080, UC085, UC087, UC088, UC092, UC093, UC095, UC097, UC098, UC099. All are **partial**, not fully supported. UC078 specifically illustrates a generic schema's inability to establish cross-field relationships; the test must not be interpreted as implementing that missing logic.

**Existing saved eval references:** the draft references checked IDs from `evals/graph.json`, `evals/timestamps.json`, `evals/actions.json`, `evals/ledger.json`, `evals/structure.json`, and `evals/integrity.json`. Not every drafted use case has a dedicated eval ID; missing IDs are explicitly empty/unknown rather than fabricated.

### What was actually checked

- Draft Python files and integration helper compiled as source.
- Referenced existing test functions were cross-checked against repository source.
- Cited eval IDs were cross-checked against the saved evaluation suites.
- **Not checked:** pytest execution of the 94 new scenarios, complete regression, CI, performance, real-world detector accuracy, or integration against changes after the baseline.

### Integration protocol for the other agent

1. Confirm the latest `main` and reconcile concurrent commit B work before applying the draft. Do not overwrite changed catalog entries.
2. Obtain and review the ZIP. Place the two new test files under `tests/`; they do not replace existing tests.
3. Run the helper from the repository root with `uc051_100_audit_draft.json` alongside it, or manually apply its validated manifest updates. It rejects unexpected IDs, missing modules, missing test functions, missing eval case IDs and conflicting status values.
4. Run the two new test modules, `pytest -q`, and affected eval suites. Preserve all existing tests; fix real defects instead of changing expected results to force green.
5. Record exact passed/failed/skipped counts and run/commit SHA; then update this audit log and the appropriate use-case docs. Do not mark `verified` merely because a test function exists.
6. Commit only Atlas-owned files and new tests. The other agent owns the reserved files listed in the Atlas handoff. Do not edit reserved code or commit B's privacy cases.
7. After commit B is on `main`, separately audit UC021, UC023, UC029, UC037 and UC048 against its actual findings and tests. Until then do not rely on pre-fix duplicate privacy findings.
8. UC101–200 are **triage only** without explicit authorization for new checkers.

### Current CI and coordination blocker

At this documentation point, latest Tests #184 on `48ee5a5f` is **failed**. The failure includes `tests/test_configuration.py::test_pii_checker_loads_only_its_declared_dependencies`, involving unexpectedly imported Cython modules; earlier runs also showed dashboard failures. These are in the reserved workstream, not addressed by this audit. The handoff asks for green CI after each push. The new draft tests remain uncommitted until they can be run and reconciled. Documentation of the failure is not a fix.

### Source-of-truth distinction

- **Committed audit:** UC003, UC005, UC006, UC007, UC018, UC019, UC020, UC024, UC042, UC044, UC050 (11).
- **Prepared, not committed:** the 21 IDs above.
- **Tests actually added to GitHub by the catalog audit:** manifest evidence test changes; **zero** of the 9 draft functions.
- **No changes to existing eval cases, checkers, reserved privacy files, or test expectations.**
