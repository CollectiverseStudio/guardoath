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


## Work package v7 — top-level test evidence check (not integrated)

The offline integration helper `apply_catalog_audit.py` now validates referenced test functions using only module-level Python AST function definitions, rather than every nested definition found by `ast.walk`. A nested helper function can no longer impersonate a pytest test. New `test_nested_function_cannot_impersonate_pytest_test` in `test_audit_apply_strict.py` verifies rejection and byte-for-byte preservation of the manifest. The v7 package is held in the originating conversation as `guardoath_catalog_audit_work_package_v7.zip`; it has **not** been merged into the repository.

**Executed offline:** `python -m unittest discover -s . -p 'test_audit_apply_strict.py' -v`: 13 unittest executions passed (includes inherited methods collected under the subclass; do not count as 13 unique new tests). No GuardOath API tests or full CI were executed for v7. The 94 draft API scenarios remain unverified.

**Next agent:** obtain v7, reconcile current `main`, run the full offline suite and GuardOath pytest, and only then integrate. The previously observed CI blocker in reserved configuration/dashboard tests remains outside this audit's file ownership. No checker, privacy code, or existing test expectation was modified.


## Work package v8 — manifest and eval integrity hardening

The offline `apply_catalog_audit.py` helper now checks that the original manifest has **exactly 200 ordered IDs UC001–UC200**, rather than merely checking for duplicate IDs. It also rejects duplicate saved eval `case_id` values and malformed eval records lacking a string `case_id`. These checks happen before manifest mutation and protect against accidentally using a truncated/reordered manifest or ambiguous evaluation evidence.

New local package-only test file `test_audit_manifest_integrity.py` adds four scenarios: missing manifest ID, reordered manifest, duplicate eval ID, and malformed eval record. All assert failure without modifying the manifest bytes. The targeted discovery command executed **8 unittest methods** (4 newly added and 4 imported from the fixture module); all passed in 17.124 seconds. These are offline synthetic-checkout tests, not GuardOath pytest or CI. The 94 drafted API scenarios remain unexecuted.

**Integration:** v8 supersedes v7. The helper and its tests are held in the work package, not pushed to GitHub. The latest known Tests workflow on `main` remains red; do not treat this as a product regression fix. The other agent must reconcile the draft with current `main`, run pytest and eval suites, and only then integrate the catalog. No reserved files, existing tests or checkers were changed.


## Competitive input-validation research and approved roadmap (2026-10-29)

Greg approved adding the architectural recommendations from six public sources (Nightfall, SnailSploit, AWS Prescriptive Guidance, VibeProd, Cisco AI Defense, ApX). The full design, source URLs, priorities, acceptance criteria, security boundaries and owner coordination are documented in [input-validation-expansion-roadmap.md](input-validation-expansion-roadmap.md).

**New workstream IDs:** IV-01 semantic input assessment (P0), IV-02 RAG/tool-response quarantine (P0), IV-03 continuous effectiveness metrics (P0), IV-04 source-code AST validation (P1), IV-05 tenant-specific policy governance (P1), IV-06 safe transformation/revalidation (P1), IV-07 posture reporting (P2). Existing local deterministic controls are listed as foundations, not as proof these seven capabilities exist.

**Implementation in this batch:** one new roadmap document and this audit note. **New checkers: 0; new test functions: 0; new eval cases: 0; changed manifest statuses: 0.** No reserved files were touched. The 21 drafted UC051–100 entries and 94 unexecuted API scenarios remain in the external v8 work package; no claim of GitHub integration. UC101–200 remain proposed.

**Verification:** documentation was committed to `main`; latest observed Tests #187 before this commit was failing. Any new CI run must be checked independently. The other agent should use the roadmap as a design backlog, not a claim of delivered capability, and coordinate before modifying the reserved engine, models, app, privacy or dashboard files.


## Implementation batch — IV-02 and IV-03 foundations (2026-10-29)

New files `guardoath/effectiveness.py`, `tests/test_effectiveness.py`, `guardoath/untrusted_content.py`, `tests/test_untrusted_content.py`, and `docs/input-validation-implementation-progress.md` were committed. The new implementation progress document records exact functionality, tests, limitations and next integration requirements.

- IV-03: Offline TP/FP/TN/FN and latency percentile accounting with explicit missing denominators; no live effectiveness claim.
- IV-02: Typed provenance for user, retrieved document and tool response surfaces; no prompt injection detection or host enforcement.
- New test functions: 7 (one parametrized across 3 trust surfaces); new eval cases: 0.
- No existing test/eval modifications, no paid provider calls, no edits to reserved files, and no change to UC101–200 implementation status.

**CI:** Pending independent verification on these commits. Earlier baseline CI failures are not addressed by this new isolated foundation. See [implementation progress](input-validation-implementation-progress.md) for the complete agent handoff.


## IV-03 follow-up: versioned comparison (2026-10-29)

New `guardoath/evaluation_comparison.py` and `tests/test_evaluation_comparison.py` provide offline paired-run regression analysis with strict matching of dataset ID/version, case IDs and ground-truth labels. Results include rate deltas and case IDs for newly missed attacks and newly flagged benign inputs. See [implementation progress](input-validation-implementation-progress.md) for the complete behavior, test functions and limitations.

**Counts:** 1 new implementation module, 1 new test file, 4 top-level test functions (including one parametrized over 3 cases), 0 new eval cases, 0 new detectors. No changes to existing tests, privacy files, engine, app, models or reserved work. Tests were committed but not run locally; check GitHub Actions on the latest commit. Previous CI failures remain a separate coordination issue.


## IV-03 follow-up: local evaluation adapter (2026-10-29)

Added `guardoath/evaluation_adapter.py` and `tests/test_evaluation_adapter.py` to translate already executed local regression reports into labeled effectiveness observations. The adapter requires separately reviewed boolean attack/benign labels and refuses incomplete, simulated, skipped/error, duplicate or mismatched cases. It is not wired into the CLI or the protected engine. Exact behavior and limitations are in [input-validation-implementation-progress.md](input-validation-implementation-progress.md).

**Change accounting:** one new implementation module, one new test file, four top-level pytest functions (two parametrized), zero saved eval changes, zero checkers, zero manifest status changes. No reserved files edited. The latest CI result must be checked independently; this commit does not claim live detection accuracy.


## IV-03 implementation: local suite execution with reviewed labels (2026-10-30)

New `guardoath/labeled_evaluation.py` composes `load_suite`, `load_policies`, the local Engine, `evaluate`, `observations_from_report`, `summarize` and `compare_runs`. New `tests/test_labeled_evaluation.py` runs existing `evals/text-security.json` with `policies/text-security.json` using explicit fixture classifications. Three new test functions were added; zero existing eval cases or checkers changed. Tests are not a live accuracy benchmark.

The new workflow remains opt-in, local-only, and does not modify the protected evaluation runner or the other agent's reserved files. See [implementation progress](input-validation-implementation-progress.md) for exact behavior and remaining verification. Latest baseline CI previously failed in reserved configuration/dashboard tests; verify new workflow status before claiming completion.

## Director coding-agent handoff (2026-10-30)

The consolidated engineering handoff is now [director-coding-agent-handoff.md](director-coding-agent-handoff.md). It links all relevant architecture, 200-use-case, implementation and coordination documents; distinguishes committed work from the unmerged v8 ZIP; lists CI failures and subsequent labeled-evaluation fixes; and prioritizes the next work. Read it before integrating the external package or modifying shared files. No catalog status or reserved implementation was changed by this documentation update.


## 2026-10-30 — Integration of previously drafted API regression tests

The baseline GitHub Actions **Tests #223** on commit `4817dcc2` completed **successfully**, allowing integration of the previously unmerged API regression scenarios. The following two **new** test files were committed without editing the Director-owned website or any existing tests:

- `tests/test_catalog_partial_boundaries.py`: four parametrized test functions, **36 cases** for UC068 (coordinate array shape/count), UC079 (exact taxonomy), UC087 (latitude/longitude bounds), and UC092 (additional-field rejection).
- `tests/test_catalog_partial_contracts.py`: five parametrized test functions, **58 cases** for UC074 (null/numeric contract), UC085 (sensor range), UC078 (explicit cross-field semantic gap), UC080 (static conditional schema), and UC092 (over-posting and missing fields).

**Total: nine new test functions, 94 parametrized scenarios.** These are narrowly scoped JSON Schema and API-contract tests, **not** evidence that domain-specific detectors, semantic interpretation, real-world agent safety, or all 200 use cases are implemented. No use-case manifest status is changed solely because tests were added. The 21 drafted audit classifications in the v8 ZIP remain unmerged pending independent evidence review.

**CI:** The new commits triggered fresh GitHub Actions runs. These tests are *committed*, not yet independently confirmed passing at the time of this documentation update. If CI fails, inspect the failing tests and fix the implementation/test fixture mismatch without weakening any existing regression or altering reserved files. Record the final CI run ID, matrix results and test counts before marking them verified.


## 2026-10-30 — Legacy-name guard adversarial test coverage

Added `tests/test_legacy_name_guard_regressions.py` with **five top-level pytest functions and eight parametrized executions**. Synthetic temporary Git repositories exercise tracked UTF-8 content across extensions (including text masquerading as `.pptx`), untracked-file exclusion, binary undecodable content, exact-path license exception, and multiple violation reporting. The target guard is `tests/test_no_legacy_project_names.py::test_tracked_text_files_contain_no_forbidden_legacy_names`.

This adds test evidence for the Director's REQ-D3 without modifying the Director-owned site, API, engine, privacy or CI files. No existing tests or saved eval cases were removed, skipped or weakened. **Verification status:** new tests are committed; latest GitHub Actions on this commit must finish before calling them CI-green. The previously added 94 API scenarios were included in successful Tests #227. These guard tests do not add content validation checkers or change any UC status.


## 2026-10-30 — UC002 evidence audit (non-privacy)

**UC002 Jailbreak detection: `review_required` → `partial`.** Narrow implemented portion: the optional input-only Azure Prompt Shields user-prompt adapter, not a local semantic jailbreak detector. Source: `guardoath/azure_prompt_shields.py`. Exact tests: `tests/test_azure_prompt_shields.py::test_real_adapter_contract_and_routing_with_fixture`, `::test_output_is_not_sent_to_prompt_api`, and `::test_unavailable_provider_remains_incomplete_without_network`. Dedicated saved eval case ID: **none identified** (`[]` in manifest). The provider is opt-in, mocked in tests, and no live accuracy/recall benchmark exists. No claim of full jailbreak protection.

Changed only `docs/use-case-manifest.json` and this audit note; no checker, test, website, Director-reserved file, or saved eval was modified. CI on the manifest commit must be verified before marking the audit integration green.


## 2026-10-30 — UC004 encoded payload audit

**UC004 Base64/hex payload inspection: `review_required` → `partial`.** The existing `guardoath/text_security.py` checker recognizes explicitly labeled `base64:` and `hex:` tokens, enforces count/length budgets, decodes one UTF-8 layer, and flags encoded text, malformed encoding and selected hidden control characters. It does not perform recursive decoding, unlabeled payload inspection, general malware detection or semantic injection analysis.

Exact tests: `tests/test_text_security.py::test_labeled_encoded_text_is_review_signal_even_when_benign`, `::test_malformed_or_non_utf8_encoded_data`, `::test_encoded_hidden_controls_and_no_payload_echo`, `::test_boundary_budgets_and_single_layer_only`. Saved eval evidence: `text-security:encoding.base64.input`, `encoding.hex.input`, `encoding.base64_hidden.input`, `encoding.hex_bad.input`, and `scope.unlabeled_base64.input`. Existing checker/evals/tests were not modified. This is an evidence-only manifest status update, not a new security feature. Verify CI on the corresponding commit.


## 2026-10-30 — UC011 hate-speech audit (non-privacy)

**UC011: `review_required` → `partial`.** The implemented portion is an optional provider-neutral `content_safety` registration and AWS Bedrock Guardrails adapter, with a `HATE` category contract. Exact modules: `guardoath/content_safety.py`, `guardoath/bedrock_guardrails.py`. Exact tests: `tests/test_content_safety.py::test_provider_can_be_replaced_without_changing_api_or_policy`, `::test_disabled_provider_is_incomplete_and_visible_as_external`; `tests/test_bedrock_guardrails.py::test_category_contract`, `::test_missing_or_partial_coverage_is_not_a_pass`. Dedicated saved eval case ID: **none identified** (`[]`).

This does not establish a local hate-speech detector, real-world sensitivity/specificity, independent calibration, or live AWS testing. A missing/disabled provider is incomplete rather than safe. No implementation, existing test or saved eval was modified; this batch changes only the Atlas-owned manifest/audit docs. New CI must be checked before calling the documentation integration green.


## 2026-10-30 — UC012 self-harm workflow evidence audit

**UC012:** retained canonical `review_required`, with `audit_classification=not_implemented`. Evidence: `guardoath/bedrock_guardrails.py` defines only five content filter categories (hate, insults, sexual, violence, misconduct); `guardoath/content_safety.py` registers an optional provider but does not implement a self-harm classifier or crisis-routing response. Exact tests: `tests/test_bedrock_guardrails.py::test_category_contract` and `tests/test_content_safety.py::test_disabled_provider_is_incomplete_and_visible_as_external`. Dedicated saved eval ID: none identified (`[]`). The presence of an optional generic content-safety adapter is not evidence of UC012 support. No checker, existing test, eval or Director-reserved file was changed.


## 2026-10-30 — UC013–016 negative-evidence audit

Four cases retain canonical `review_required` and gain `audit_classification=not_implemented` with exact module/test references and empty dedicated eval ID lists:

| ID | Gap | Existing evidence proving the boundary |
|---|---|---|
| UC013 | Profanity masking not implemented | `guardoath/pii.py`; `tests/test_pii.py::test_real_local_findings` covers sensitive identifiers, not profanity transformation |
| UC014 | Brand political/religious neutrality not implemented | `guardoath/content_safety.py`, `guardoath/bedrock_guardrails.py`; `tests/test_content_safety.py::test_disabled_provider_is_incomplete_and_visible_as_external`, `tests/test_bedrock_guardrails.py::test_category_contract` |
| UC015 | Extremism/radicalization detection not implemented | Same optional-provider category contract; no specialized extremist reference source or classifier |
| UC016 | Image CSAM detection not implemented | `guardoath/ingress.py`; `tests/test_foundations.py::test_formats_are_enforced` documents JSON/text check-envelope boundaries, not an image classifier |

No dedicated saved eval case ID was identified for these capabilities. These are negative coverage audits, **not** proof that the cited tests directly test absence of the requested capability. Do not advertise these as supported. No new detectors, tests, saved evals or Director-reserved code were changed.


## 2026-10-30 — UC017 cyberbullying audit

**UC017 `review_required` → `partial`.** Existing optional Bedrock content safety adapter exposes provider `HATE` and `INSULTS` category contracts for input/output. Exact modules: `guardoath/bedrock_guardrails.py`, `guardoath/content_safety.py`. Exact tests: `tests/test_bedrock_guardrails.py::test_category_contract`, `::test_missing_or_partial_coverage_is_not_a_pass`; `tests/test_content_safety.py::test_disabled_provider_is_incomplete_and_visible_as_external`. Dedicated saved eval case ID: **none identified** (`[]`).

The adapter is opt-in and provider-dependent. It does **not** establish repeated harassment detection, sarcasm/intent interpretation, youth-specific risk assessment, or a locally calibrated cyberbullying model. No checker, test or saved eval was changed; this is an evidence-only classification. The new GitHub Actions run must be checked before calling the audit CI-green.


## 2026-10-30 — UC022 card-number masking audit

**UC022 `review_required` → `partial`.** Existing `guardoath/pii.py` supports selected card-number patterns with Luhn validation and policy-configured redaction. Exact tests: `tests/test_pii.py::test_real_local_findings`, `::test_benign_lookalikes`, `::test_api_output_and_logs_do_not_echo_sensitive_values`. Saved eval IDs: `privacy:card.input` and `privacy:card.output`.

Limits: selected formats and configured policy, not blanket payment-data detection, processor integration or PCI DSS compliance. The audit is evidence-only; no checker, existing test, saved eval or Director-reserved file was modified. CI on this commit remains to be verified.


## 2026-10-30 — UC025–027 negative coverage audit

Three cases retain canonical `review_required` with `audit_classification=not_implemented`. No dedicated saved eval cases were identified.

- **UC025 Geographic privacy consent:** `guardoath/pii.py`, `guardoath/content_safety.py`; `tests/test_pii.py::test_real_local_findings`, `tests/test_content_safety.py::test_disabled_provider_is_incomplete_and_visible_as_external`. Identifier matching does not prove jurisdictional consent or cross-border transfer authorization.
- **UC026 Biometric consent token:** `guardoath/ingress.py`, `guardoath/pii.py`; `tests/test_foundations.py::test_formats_are_enforced`, `tests/test_pii.py::test_real_local_findings`. Bounded JSON/text requests and identifier detection do not verify biometric consent or cryptographic authorization.
- **UC027 Third-party copyright/IP:** `guardoath/secrets_check.py`; `tests/test_secrets_check.py::test_supported_candidates`, `::test_no_verification_or_network`. Literal confidential phrase/credential checks are not copyright ownership, license or infringement analysis.

These are **negative adjacent-evidence audits**, not dedicated tests proving absence of functionality. No checker, existing test, eval case or Director-reserved file was changed. New CI verification pending.


## 2026-10-30 — UC028 hashed log identities audit

**UC028 `review_required` → `partial`**, strictly for an adjacent exact-hash integrity contract. Existing `guardoath/data_quality.py` verifies submitted bytes against a trusted configured SHA-256 digest. Tests: `tests/test_data_quality.py::test_data_quality_api_expectations` and `::test_digest_reference_is_private_and_not_caller_controlled`. Saved eval evidence: `integrity:valid.exact-utf8.input` and `integrity:valid.exact-base64.input`.

**Critical gap:** Digest equality is **not** a privacy-preserving identity hashing implementation. No log identity pseudonymization, salt/key management, origin attestation or re-identification assessment exists. The manifest `partial` status reflects this narrow adjacent capability, not successful fulfillment of the requested identity-privacy use case. No checker, test, eval or Director-reserved file was changed.


## 2026-10-30 — Audit evidence integrity regressions

Added **four new test functions** in `tests/test_catalog_evidence_quality.py` (commit `f360122b`), without editing existing tests or Director-reserved files:

1. Audited case IDs and module/test/eval reference lists must not contain duplicates; audit notes must not be empty.
2. Negative `not_implemented` audits cannot be marked supported or verified; they retain `review_required` with adjacent code/test evidence.
3. `partial` audits must include explicit source modules, test references and a saved-eval ID list (which may legitimately be empty).
4. Every cited saved eval ID must resolve to a real unique case in the named suite.

These are **catalog integrity tests**, not content security detectors or proof that 200 capabilities exist. They strengthen evidence bookkeeping for the ongoing audit. New GitHub Actions CI is pending; no local test run is claimed.


## 2026-10-30 — UC033 extracted receipt JSON contract

Added **12 parametrized input/output test executions** in new `tests/test_catalog_receipt_contracts.py` (`7f27c597`): six extracted JSON receipt records × two stages. Tests cover valid merchant/date/total, missing required total, blank merchant, invalid date, negative total and unexpected field. Existing JSON Schema checker: `guardoath/checkers.py`. The UC033 manifest is `partial` (`8fe77273`) with exact new test reference and no dedicated saved eval.

**Limitations:** This is only a schema contract for **already extracted JSON**, not receipt OCR, source-document authenticity, image legibility, semantic receipt completeness, or fraud detection. No Director-reserved code, existing test or eval suite changed. New CI is pending.


## 2026-10-30 — Media-ingress boundary tests and UC031/032/034/035/036

Added **10 parametrized HTTP ingress regressions** in `tests/test_catalog_media_ingress.py` (commit `2e341f5f`): eight unsupported media types (PNG, JPEG, WebP, DICOM, PDF, multipart, WAV, MP4) and two non-UTF-8 JSON charsets. These assert the existing `guardoath/ingress.py` rejects unsupported request types with HTTP 415; no image, document, medical or audio analysis is claimed.

Five use cases retain canonical `review_required` with `audit_classification=not_implemented` in `docs/use-case-manifest.json` (commit `cdae3326`): **UC031 ID image legibility; UC032 invoice tampering; UC034 medical scan quality; UC035 deepfake detection; UC036 handwriting legibility**. Each cites the exact new ingress tests and no dedicated saved eval. The test only proves the direct HTTP media boundary; externally extracted JSON fields may still be schema-validated. No existing tests, saved evals, Director-reserved code or deployment files were modified. CI pending.

## 2026-10-30 — REQ-D5 fixed and observe/enforce regression matrix

GitHub Actions **Tests #293** (fix commit `a6a764b0`) and **Tests #294** (coordination commit `696b1644`) both completed **successfully**, confirming the receipt contract fixture's `mode="enforce"` fix. The original 12 cases and assertions remain intact. Added **12 further parametrized observe-mode scenarios** to `tests/test_catalog_receipt_contracts.py` (commit `2ec9875f`), testing the same six receipt records on both input/output stages. These explicitly assert that observe mode reports findings and recommends blocking on schema violations, while `effective_action` stays `allow`; valid records remain allowed with no findings. This guards against future confusion between detection and enforcement. No production policy was changed. The new 12-case addition is committed; CI verification is pending.

## 2026-10-30 — UC038–040 document/image unsupported boundaries

**UC038, UC039, UC040** retain canonical `review_required` and gain `audit_classification=not_implemented`. Adjacent source: `guardoath/ingress.py`; exact regression: `tests/test_catalog_media_ingress.py::test_uc031_032_034_035_036_binary_media_is_not_analyzed`; dedicated saved eval IDs: none. The JSON-only ingress boundary is **not** uploaded-document MIME/magic-byte classification, visual or cryptographic signature verification, or steganography detection. These are negative-evidence audits only. OCR/document/image subsystem is separately proposed in `docs/document-analysis-implementation-proposal.md` and awaits approval; no new checker or Director-reserved code changed. GitHub Tests #298 was green on the preceding main commit.

## 2026-10-30 — Policy mode test matrix

Added `tests/test_catalog_policy_mode_matrix.py` (commit `d2f75db8`): 20 parametrized scenarios across two stages, two policy modes and five payloads. They verify consistent schema findings and recommendations, but `effective_action=block` only with `mode="enforce"`. No existing tests or Director-owned files were changed. CI verification pending.
