# Implementation backlog

- [x] Sequential conversation demo: combined general text profile, input gate, separate stage buttons, invalidation tests and guide.
- [ ] Connect an explicitly configured model provider between the input and output stages.

- [x] Separate input and output example sets: only their own editor/format changes, no implicit profile changes, output examples preserve input results, varied reply fixtures and browser coverage.

## Milestone 1 — local checking foundation

- [x] Citation-consistency batch: trusted local source catalog, exact quotations/markers, explicit truth/entailment gaps, 100 regression expectations and API/browser/launcher/guide coverage.
- [x] Proposed-tool-call batch: trusted exact allowlist/per-tool schemas, no execution, lossy-token rejection, 100 regression expectations and API/browser/launcher/guide coverage.
- [ ] General claim entailment/truth, live reference verification, source authenticity/freshness and authorization/sandbox execution controls remain separate requirements.


- [x] Strict input and output contracts.
- [x] Real deterministic JSON Schema validation.
- [x] Structure group batch: dedicated contracts, safe first-problem explanations, timezone-aware time correction, seven new examples per stage, 100 regression cases and guide/launcher/CI coverage.
- [x] Preserve raw decimal tokens in comparison checks; do not round through binary floats.
- [ ] Exact decimal schema arithmetic and JSON transformation preservation, exhaustive conformance coverage and hard CPU isolation for schemas.
- [x] Versioned startup policy snapshots.
- [x] Observe/enforce decisions and required-check failure handling.
- [x] Application-scoped API credentials and sanitized validation errors.
- [x] Explicit safety/bias simulators and meaningful automated tests.
- [x] Local setup instructions and CI test workflow.

## Milestone 2 — usable policy configuration and local detectors

- [x] Define a versioned policy file format and load validated configuration.
- [x] Add an optional local Presidio adapter for email and credit-card patterns, without NLP model downloads.
- [x] Add context-aware U.S. SSN candidates and policy-controlled, revalidated redaction of supported identifiers.
- [x] Complete the supported privacy-field batch: labeled phone, birth-date, medical-record, health-plan and bank-account candidates; revalidated redaction; 100 regression expectations, API/gate/browser coverage and guide.
- [ ] Extend privacy coverage to names, addresses, medical narratives, additional identifiers and customer-specific requirements with reviewed accuracy data.
- [x] Add bounded offline secret-pattern checks and real dashboard coverage for selected credentials/private-key markers.
- [x] Complete the supported Secrets group batch: additional provider patterns, opt-in quoted assignments, encoded inspection, literal confidential terms, 100 regression cases, API/gate/browser coverage and user guide.
- [ ] Evaluate semantic confidential-document classification, additional provider formats and password/obfuscation handling beyond the documented heuristics.
- [x] Use a single canonical content value with explicit text/JSON format to prevent mismatched payloads.
- [x] Add ingress byte limits, body-read deadline, and bounded JSON parsing.
- [x] Enforce supported schema formats and declarative numeric/date comparisons.
- [x] Business group batch: raw-decimal comparisons, explicit failure reasons, all six operators, date/pointer/section cases, 100 regression expectations, independent examples and guide/launcher/CI coverage.
- [ ] Trusted server-side reference limits, customer-specific business semantics and reviewed adequacy checks beyond submitted-value consistency.
- [x] Add stage-specific actions and ten shared input/output coverage groups.
- [x] Add configurable labeled-text completeness rules, stage-specific findings, and dashboard examples.
- [x] Add bounded hidden/control-character and explicit Base64/hex review signals with dashboard examples.
- [x] Complete the supported local text-security batch: fixed signal/reason diagnostics, 100 cases, Unicode/encoding/JSON/shared-budget boundaries, explicit semantic gaps, independent examples and guide/launcher/CI coverage.
- [x] Implement an opt-in Azure Prompt Shields input adapter, external-routing disclosure, unknown-cost reporting, and offline provider-contract tests.
- [x] One-layer encoded PII batch: shared encoding budgets, existing identifier recognizers/decoded JSON hints, safe incomplete states, raw-redaction/gate protections, 100 expectations and guide/browser/launcher/CI coverage.
- [ ] Activate/calibrate semantic prompt-attack detection; extend unlabeled/nested/other obfuscations and Unicode coverage with reviewed data.
- [x] Gate registered external checks behind explicit policy permission and all configured real local passes, including redaction rechecks.
- [ ] Expand privacy-gate coverage beyond supported PII/secrets; improve combined required-external/redaction workflows.
- [x] Add per-application token-bucket and concurrent-check limits plus process-level check/ingress budgets and retry guidance.
- [ ] Add shared multi-worker/multi-server quotas, application-specific plans, and worker CPU/job budgets.
- [x] Add a cross-platform dashboard launcher with isolated setup, fresh keys, and browser opening.
- [x] Add bounded local regression suites, source-free JSON/HTML reports, group filtering, and an easy launcher option.
- [x] One-command collection of all 14 bundled local regression suites, source-free index/per-group reports, nonzero mismatch exits and preserved prior outputs.
- [x] Compare saved local regression baselines with changed outcomes, mismatches, case-set changes, and separate timing observations.
- [ ] Add independently reviewed accuracy/calibration datasets and stronger fixture/provenance versioning.
- [x] Build a local playground with input/output findings, held answer previews, and prominent simulation labels.

## Milestone 3 — provider-independent integration and live calibration

- [x] Separate hosting from validation-provider selection; stable content_safety interface and provider replacement test.
- [x] Optional Bedrock content adapter: input/output, five active categories, detect-only findings, local-first gate, safe errors, retained worker slots, 100 offline contract cases plus integration/browser coverage.
- [ ] Demo hosting/deployment: handled separately by Greg. No Lambda/CloudFront implementation in the validation-check batch.
- [ ] Independently review and calibrate real harmful-content examples; no accuracy claim from protocol fixtures.
- [ ] Add additional managed safety adapters as requirements justify; maintain documented differences in coverage.


- [ ] Configure an approved Azure resource, region, retention/data terms, and test budget; run a live Prompt Shields smoke/calibration suite.

- [ ] Choose managed safety service, region, data routing rules, and test budget.
- [ ] Add credentials through a secret manager or local environment, never source control.
- [x] Test provider errors and deadlines using synthetic local SDK fixtures (not live calibration).
- [ ] Add real provider cost metadata and approved fallback behavior.
- [ ] Evaluate Tokatlas compatibility for optional contextual evaluator calls.

## Milestone 4 — contextual bias evaluation

- [x] Local matched-decision consistency: strict structured evidence, exact numeric comparisons, all-record comparability, safe review findings, 100 regression expectations, independent examples and launcher/CI/guide coverage.
- [ ] Add independently reviewed matched-model trials and validate decision factors/attribute choices; synthetic consistency fixtures are not fairness accuracy data.


- [ ] Approve initial bias rubrics and reviewed examples.
- [ ] Implement contextual findings with uncertainty and policy references.
- [ ] Add repeated matched-scenario tests and reviewer feedback.
- [ ] Maintain separate tuning and evaluation cases.

## Milestone 5 — hosted pilot

- [ ] Tenant-aware PostgreSQL persistence and immutable published policies.
- [ ] Administrator/reviewer/reader roles, credential rotation, and audit history.
- [ ] Customer-controlled retention/deletion and redacted evidence storage.
- [ ] Dashboard, evaluation worker, and regression reports.
- [ ] Dependency review, load tests, and pilot accuracy/latency/cost gates.
- [ ] Enable blocking only after customer review of observe-mode results.


## Additional acceptance catalog — use cases 51–100

- [x] Document all 50 additional requirements with current scope, positive/benign/edge acceptance criteria and remaining dependencies: docs/additional-use-cases.md.
- [x] Add 77 executable unit/API/engine cases for 17 scoped/partial existing controls. No placeholder test reports an unimplemented detector as passed.
- [ ] Semantic/context/session evaluation: UC051–060 (except existing transport/size controls), UC066, UC071, UC094. Requires reviewed rubrics, trusted context and independent evaluation.
- [ ] Behavior/correlation/telemetry/corpus services: UC061–070 beyond existing quotas/array limits; shared tenant/session/event handling remains separate.
- [ ] Statistical drift, graph/sequence algorithms, derived field rules and taxonomy/quarantine workflows: UC073–080 beyond the tested deterministic subset.
- [ ] Specialized medical/CAD/code/bio/audio/geospatial/ledger/telematics pipelines: UC081–090 beyond scalar/box/submitted-total checks, with approved fixtures and domain review.
- [ ] URL reputation, orchestration graphs, dataset checksums, regulated-entity resolution, tokenizer contracts and proof verification: UC091–100 beyond existing over-posting/UTF-8/phrase/cap controls.
- [ ] Live publication/normalization/repair behaviors need explicit contracts and preservation/revalidation tests; no automatic sanitization or silent truncation is enabled.

## Data-quality batch (0.24.0)

- [x] Exact ledger line-item arithmetic/currency, timestamp sequence order, directed-graph structure and trusted SHA-256 matching: bounded contracts, 400 regressions, API/gate/privacy tests, independent dashboard examples, launcher/CI and guide.
- [ ] Ledger accounting/fraud semantics, cross-request telemetry order/freshness, graph relationship legality/package compatibility and checksum provenance/streaming uploads remain separate requirements.

## Extended validation catalog and orchestration architecture (UC101–200)

- [x] Register 100 additional proposed scenarios and their required positive, negative, boundary, failure and evidence acceptance categories: [UC101–200](use-cases-101-200.md). This is requirements coverage, not detector implementation.
- [x] Record target architecture: trusted policy enforcement/orchestration, bounded schema checks, specialist adapters, allow/quarantine/block/review, and auditable evidence.
- [ ] P0 agentic enforcement UC101–110: trusted tool authorization, state-machine transitions, recursion and cost budgets, sandbox boundaries, downstream injection handling; implement enforcement outside the LLM with real API tests.
- [ ] P1 selective Web3/network and regulated-healthcare adapter contracts UC131–140 and UC171–180; require domain-specific validation and qualified reviewers.
- [ ] P2 multimodal, edge, aerospace, climate and industrial adapters UC111–130, UC151–160, UC181–200; do not suppress safety-critical alarms.
- [ ] P3 quantum/deep-tech adapters UC161–170 subject to real demand, fixtures and specialist expertise.
- [ ] For every implemented row, update user guide, dashboard examples, CI, and coverage status with evidence. Never count a proposed acceptance test as a passing implementation.
