# Implementation backlog

## Milestone 1 — local checking foundation

- [x] Strict input and output contracts.
- [x] Real deterministic JSON Schema validation.
- [x] Versioned startup policy snapshots.
- [x] Observe/enforce decisions and required-check failure handling.
- [x] Application-scoped API credentials and sanitized validation errors.
- [x] Explicit safety/bias simulators and meaningful automated tests.
- [x] Local setup instructions and CI test workflow.

## Milestone 2 — usable policy configuration and local detectors

- [x] Define a versioned policy file format and load validated configuration.
- [x] Add an optional local Presidio adapter for email and credit-card patterns, without NLP model downloads.
- [x] Add context-aware U.S. SSN candidates and policy-controlled, revalidated redaction of supported identifiers.
- [ ] Extend coverage to other identifiers and customer-specific privacy requirements.
- [x] Add bounded offline secret-pattern checks and real dashboard coverage for selected credentials/private-key markers.
- [ ] Extend provider formats and evaluate context-aware password/confidential-document detection.
- [x] Use a single canonical content value with explicit text/JSON format to prevent mismatched payloads.
- [x] Add ingress byte limits, body-read deadline, and bounded JSON parsing.
- [x] Enforce supported schema formats and declarative numeric/date comparisons.
- [x] Add stage-specific actions and ten shared input/output coverage groups.
- [x] Add configurable labeled-text completeness rules, stage-specific findings, and dashboard examples.
- [x] Add bounded hidden/control-character and explicit Base64/hex review signals with dashboard examples.
- [x] Implement an opt-in Azure Prompt Shields input adapter, external-routing disclosure, unknown-cost reporting, and offline provider-contract tests.
- [ ] Activate and calibrate semantic prompt-attack detection against live approved fixtures; evaluate encoded PII/secrets and additional Unicode coverage.
- [x] Gate registered external checks behind explicit policy permission and all configured real local passes, including redaction rechecks.
- [ ] Expand privacy-gate coverage beyond supported PII/secrets; improve combined required-external/redaction workflows.
- [x] Add per-application token-bucket and concurrent-check limits plus process-level check/ingress budgets and retry guidance.
- [ ] Add shared multi-worker/multi-server quotas, application-specific plans, and worker CPU/job budgets.
- [x] Add a cross-platform dashboard launcher with isolated setup, fresh keys, and browser opening.
- [x] Add bounded local regression suites, source-free JSON/HTML reports, group filtering, and an easy launcher option.
- [ ] Add independently reviewed accuracy/calibration datasets and versioned baseline comparisons.
- [x] Build a local playground with input/output findings, held answer previews, and prominent simulation labels.

## Milestone 3 — first live integration

- [ ] Configure an approved Azure resource, region, retention/data terms, and test budget; run a live Prompt Shields smoke/calibration suite.

- [ ] Choose managed safety service, region, data routing rules, and test budget.
- [ ] Add credentials through a secret manager or local environment, never source control.
- [ ] Test provider errors and deadlines using recorded synthetic fixtures.
- [ ] Add real provider cost metadata and approved fallback behavior.
- [ ] Evaluate Tokatlas compatibility for optional contextual evaluator calls.

## Milestone 4 — contextual bias evaluation

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
