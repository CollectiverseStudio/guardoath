# GuardOath — Director coding-agent handoff

Updated 2026-10-30. **Audience: the other coding agent directing/coordinating GuardOath development.** This is an engineering handoff, not an executive sales summary. Read the linked source documents and current GitHub Actions before modifying code.

## 1. Repository, scope and coordination

- Repository: `https://github.com/CollectiverseStudio/guardoath`, branch `main`.
- Follow `AGENTS.md` and the previously approved Atlas catalog-audit handoff: no force pushes, no deleting/weakening tests, no unsupported completion claims. Coordinate before touching files reserved for the other workstream.
- Source of truth: repository files and actual CI results, not conversational progress estimates.
- The current implementation includes working deterministic checks, local evaluation suites, tenant/billing/override foundations and a partially implemented agent-safety framework. **It is not a production-enforced AI gateway or a verified implementation of all 200 use cases.**

## 2. Required reading — complete documentation map

**Architecture and operations**
- [README](../README.md), [AGENTS](../AGENTS.md), [user guide](user-guide.md), [specification](specification.md), [backlog](backlog.md).
- [Validation packs](validation-packs.md), [tenant overrides and billing](tenant-overrides-billing.md), [input override design](input-overrides.md), [agentic safety acceptance](agentic-safety-acceptance.md).
- [Input validation expansion roadmap](input-validation-expansion-roadmap.md), [implementation progress](input-validation-implementation-progress.md), [catalog audit and coordination log](catalog-audit-notes.md).

**200-use-case evidence**
- [UC001–050](use-case-coverage.md), [UC051–100](additional-use-cases.md), [UC101–200](use-cases-101-200.md), [machine-readable manifest](use-case-manifest.json).
- [Privacy coverage](privacy-coverage.md), [privacy pattern coverage](privacy-patterns-coverage.md), [text security](text-security-coverage.md), [secrets](secrets-coverage.md), [structure](structure-coverage.md), [business](business-coverage.md), [evidence](evidence-coverage.md), [actions](actions-coverage.md), [data quality](data-quality-coverage.md), [provider decisions](provider-decisions.md).
- See `evals/*.json`, `policies/*-batch.json` and `tests/` for executable evidence; do not infer implemented status from a catalog description.

## 3. What Atlas has committed

**Catalog audit:** 11 cases with evidence, including UC003, UC005, UC006, UC007, UC018, UC019, UC020, UC024, UC042, UC044 and UC050. Unsupported capabilities retain canonical `review_required` plus `audit_classification=not_implemented`; partial cases use `partial`. The evidence test checks cited source modules, test function names and existing eval IDs. Details and exact case references: [catalog audit notes](catalog-audit-notes.md).

**IV-02 untrusted provenance foundation**
- `guardoath/untrusted_content.py`, `tests/test_untrusted_content.py`.
- Distinguishes user input, retrieved document and tool response; records trusted tenant/app/source IDs; bounds payloads.
- Does **not** detect prompt injection or enforce LLM role separation or downstream ingestion.

**IV-03 offline security effectiveness foundation**
- `guardoath/effectiveness.py`, `tests/test_effectiveness.py`: TP/FP/TN/FN, recall, false-positive/bypass rates and P50/P95/P99 latency.
- `guardoath/evaluation_comparison.py`, `tests/test_evaluation_comparison.py`: versioned paired runs, strict dataset/case/label equality and newly missed/false-positive case IDs.
- `guardoath/evaluation_adapter.py`, `tests/test_evaluation_adapter.py`: transforms local evaluation reports into observations only with separately supplied reviewed attack/benign labels; rejects incomplete/simulated/invalid evidence.
- `guardoath/labeled_evaluation.py`, `tests/test_labeled_evaluation.py`: executes existing local suite/policy checks and connects the metrics/comparison pipeline. This is opt-in Python API; no paid provider calls, automatic CI thresholding or independent attack efficacy benchmark.

**New test failure correction in progress:** Tests #207 showed three failures in the new labeled-evaluation integration tests: using a single-policy file for a multi-policy suite and attempting to score incomplete cases. Subsequent commits switched to the text-security batch policy, added explicit case selection and corrected selected-case assertions. These changes must be validated in the next CI run; do not assume they pass. The baseline privacy dependency-isolation and dashboard failures remain separate.

**Documentation:** [expansion roadmap](input-validation-expansion-roadmap.md) defines seven workstreams (IV-01 to IV-07), their priorities, evidence gates, privacy/tenant boundaries and acceptance criteria. [Implementation progress](input-validation-implementation-progress.md) records precise limitations and next steps.

## 4. Prepared but NOT committed: separate catalog audit work package

A separate artifact, `guardoath_catalog_audit_work_package_v8.zip`, was prepared in the originating Atlas conversation and **is not in this Git repository**. Ask Greg or Atlas to transfer that ZIP if you need to integrate it. Do not treat the ZIP as already merged.

The ZIP contains `guardoath_audit/README.md`, `uc051_100_audit_draft.json`, `uc101_200_feasibility_triage.json` and `.md`, `apply_catalog_audit.py`, builder scripts, offline safety/integrity tests and two new draft API test files:
- `test_catalog_partial_boundaries.py`
- `test_catalog_partial_contracts.py`

The package describes **21 drafted UC051–100 classifications**, **94 parametrized API scenarios**, and **100 feasibility rankings for UC101–200**. These are **not** merged, and the 94 API scenarios have **not** passed against GuardOath. Offline synthetic-checkout tests and Python compilation are not substitutes for GuardOath pytest and CI. UC101–200 remain `proposed`; do not create new domain checkers without Greg's approval.

## 5. CI blockers and validation

As of the last independently inspected run, [Tests #207](https://github.com/CollectiverseStudio/guardoath/actions/runs/41052978418) failed:
- `tests/test_configuration.py::test_pii_checker_loads_only_its_declared_dependencies` (Cython modules unexpectedly present); this test belongs to the other workstream's reserved files.
- Three `tests/test_labeled_evaluation.py` failures; corrections were committed afterward and require verification.
- Dashboard browser assertions also failed. Windows launcher and optional AWS adapter jobs passed.

**Action:** fetch the latest workflow for the current `main` SHA, inspect job logs and rerun the full matrix after fixes. Do not weaken tests or report green CI until every required job passes. Preserve existing eval cases and fixtures.

## 6. Prioritized next engineering actions

1. **Stabilize CI.** Confirm the labeled-evaluation fixes; coordinate the dependency-isolation and dashboard failures with the owner of reserved files. Keep changes small and document exact commit/test counts.
2. **Integrate the separate v8 audit package** only after reconciling current `main`, running its 94 scenarios and verifying the manifest references. Add new tests without replacing existing tests. Preserve exact audit evidence and canonical status vocabulary.
3. **Finish UC001–100 evidence review**, starting with non-privacy cases. Defer UC021, UC023, UC029, UC037 and UC048 until the other agent's privacy duplicate-finding commit B is confirmed. Names and locations are not detected by NLP; never imply otherwise.
4. **UC101–200:** feasibility triage only; no new checker implementations without explicit approval.
5. **IV-03:** add reviewed labeled holdouts, trusted report output, confidence/denominator reporting and optional CI regression gates only after baseline green. Fixture labels do not prove real-world attack recall.
6. **IV-02:** wire provenance and indirect-injection validation to actual RAG/tool-response ingestion boundaries with tenant and egress authorization. The host must enforce deny decisions; metadata alone is insufficient.
7. **Multi-tenancy/commerce:** complete persistent tenant administration, controlled single-use overrides, durable usage outbox, Stripe metering and optional Tokatlas partner settlement. Current code is foundational; do not claim billing is live.

## 7. Change accounting and reporting requirements

For every future batch, report: changed paths, commit SHA, old/new use-case statuses, source module and exact test/eval IDs, added test counts, CI run number/results, reserved-file coordination, and documentation changes. Maintain [catalog audit notes](catalog-audit-notes.md) and [implementation progress](input-validation-implementation-progress.md). Do not claim tests were executed locally unless they were.

**Critical distinction:** GitHub contains the 11 committed audit classifications and IV-02/IV-03 code; the 21 additional catalog classifications, 94 API scenarios and UC101–200 feasibility rankings remain in a separate unmerged ZIP.
