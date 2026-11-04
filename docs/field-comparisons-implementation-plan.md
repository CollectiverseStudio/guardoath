# Field comparisons — rule architecture, use cases and acceptance contract

**Design status (2026-11-04):** 30 proposed *tenant-configurable relationship templates*, not 30 distinct checking algorithms. Director commit `3e6259e475911037775dc30607012337f49f0ce5` integrated dynamic tenant-owned field-comparison rule entries into the tenant rule list, but **did not remove the unapproved comparison-count ceiling** or activate all 30 templates for every tenant. See integration evidence below. **Category:** Business rules → Field comparisons. **Existing checker:** `cross_field` in `guardoath/business.py`. **Source of truth:** [machine-readable inventory](field-comparisons-rule-inventory.json). **Contract tests:** `tests/test_field_comparisons_inventory.py`.

## Scope and rule count

**First release scope: 30 concrete relationship/use-case templates** — **26 numeric and 4 ISO-date**. These are reusable configured rules rather than 30 separate implementations of `cross_field`. The existing engine provides **6 operators × 2 types = 12 primitive combinations** (`lt`, `le`, `eq`, `ge`, `gt`, `ne` × `number`, `date`); primitives are not additional catalog entries. The 30 examples cover commerce, finance, insurance, retail, logistics, transport, manufacturing, cold chain, healthcare, life sciences, research, energy, environment, agriculture, construction, workforce, access governance, asset management and compliance. The inventory assigns each `FC-001`–`FC-030` a stable ID, use case, stage scope, JSON Pointers, relation, type, positive fixture, benign fixture and expected finding.

Existing examples `date_order` and `refund_limit` in `policies/business.json` and `guardoath/demo_tenant_defaults.json` already demonstrate the engine. The 30 inventory examples are candidate templates. As of `3e6259e4`, a tenant can independently configure any comparisons **actually present on its own record**, and the record's generated rule entries appear only to that tenant; the 30 inventory templates are **not automatically installed** on every tenant.

## Explicit scope correction — no 50-per-tenant business limit

Greg has explicitly rejected any 50-comparisons-per-tenant product limit (2026-11-04). The existing `TenantRecord.comparisons` `max_length=50` is an inherited **implementation constraint**, not an accepted requirement; Codex must flag it for coordinated removal or replacement with budget-based safeguards. The first 30 proposed templates are just an initial starter inventory; they do not limit how many comparisons a customer can define. Shared schema/API edits still require Director approval conveyed by Greg.

## Integration evidence — commit `3e6259e4` (November 4, 2026)

Director implementation commit `3e6259e475911037775dc30607012337f49f0ce5` added `guardoath/record_rules.py`, a tenant-scoped `record_rules` payload on `GET /v1/applications/current`, the rule table display, per-record validation of allowed `business.<rule_id>` and `completeness.<rule_id>` settings, and `tests/test_record_rules.py` including cases for all 30 templates, own-action override, same-engine multi-tenant isolation and private-versus-public API visibility. It also added 118 separate static catalog rules (397 global static rules total). A dynamic tenant comparison is **not** one of those 397 global rules.

At that SHA, GitHub Actions [Tests run 41895001682](https://github.com/CollectiverseStudio/guardoath/actions/runs/41895001682) completed **13/13 successful jobs** including Python 3.11 **17,923 passed / 4 skipped**, dashboard **193 Playwright passed** and isolated wheel evidence **203 Sigma rules / 213 required assets**. [CodeQL 41895001985](https://github.com/CollectiverseStudio/guardoath/actions/runs/41895001985) completed **3/3 successful analyses**. These are repository CI results, not deployed hosted acceptance.

**Still open, FC-AC-08 / product blocker:** both `TenantRecord.comparisons` (`guardoath/tenant_record.py`) and `Policy.comparisons` (`guardoath/models.py`) retain `max_length=50`, a constraint Greg explicitly rejected. `TenantRecord.rule_actions` is capped at `len(RULES)` (397 currently), and `Policy.rule_actions` at 1000; these independent ceilings may eventually prevent a tenant from configuring a setting for each of its own rules. Do not replace them with an equally arbitrary cap. The application must retain sensible resource limits, but protect via measured/request-specific cost, payload size, evaluation time, pagination and controlled concurrency instead.

**Scale warning to test before lifting ceiling:** `record_rules(record)` generates examples for every comparison. For each rule, `_comparison_document` iterates all comparisons, repeatedly JSON-encodes and decodes increasingly large synthetic documents, then repeats for positive and benign samples. That algorithm has at least quadratic growth with the number of comparisons (possibly worse depending on document size and shared pointers). Codex should benchmark realistic 30/51/100/250/500-rule tenants, verify memory/latency/payload and that a tenant with >50 independent rule actions is valid after the Director-owned model change, and propose lazy example generation or an explicit bounded/paginated presentation strategy. A performance safeguard is not a rule-count product ceiling.

**Still open, FC-AC-05/07/11:** each comparison has a stage scope, but the live implementation has one `rule_actions` map rather than demonstrably independent Input and Output actions for the same comparison. Do not claim per-stage action independence until tested against the shipped tenant schema/API/dashboard.

## Existing implementation and integration gap

- `guardoath/business.py`: strict bounded JSON with duplicate-key rejection, decimal-preserving parse and ISO dates, JSON Pointer resolution, deterministic per-comparison findings `business.<rule_id>`; no arithmetic expressions, cross-document references, currency conversions, time zones or external lookups.
- `guardoath/models.py::FieldComparison`: `rule_id`, absolute JSON Pointer `left/right`, `operator`, `value_type` and `stages`; `guardoath/tenant_record.py` currently imposes an unapproved `max_length=50` implementation ceiling and stage-scopes checks.
- `guardoath/rule_catalog.py::RULES` contains the current global static catalog, **not tenant-defined** `business.<rule_id>`. Since `3e6259e4`, `guardoath/record_rules.py` and `TenantRecord` allow only dynamic rules defined on the current tenant's record. The per-tenant listing/authorization gap is addressed by committed code and CI, while scale and per-stage action acceptance remain open.
- `guardoath/engine.py` already supports rule-level action decisions for findings; preserve the original finding ID and apply the **tenant's** configured action. `mode=observe` cannot be described as an enforced Block. Use `mode=enforce` in enforcement tests.
- The current dashboard and hosted API are Director-owned paths. Codex may implement/test non-overlapping checker/evidence code; edits to `guardoath/app.py`, `tenant_record.py`, `models.py`, `engine.py`, `rule_catalog.py`, `guardoath/static/*`, deployment, workflow or packaging require explicit ownership permission from the user/Director before being made. This handoff does not grant that permission.

## Why these use cases matter

| Family | Example | Security/business expectation | Legitimate look-alike |
|---|---|---|---|
| Refund and payment bounds | Refund 120 vs purchase 100 | Finding on submitted inconsistency | Refund 40 vs purchase 100 |
| Finance/insurance ceilings | Claim 250 vs limit 200 | Finding on exceeded bound | Claim 100 vs limit 200 |
| Stock/transport limits | Requested 15 vs stock 10 | Finding on exceeded capacity | Requested 5 vs stock 10 |
| Cold chain and manufacturing | Temperature 5 vs strict maximum 5 | Strict boundary finding | Temperature 3 vs maximum 5 |
| Healthcare/research fields | Proposed dose 20 vs prescribed 10 | Consistency finding only, **not clinical authorization** | Dose 5 vs prescribed 10 |
| Operations/environment | Emissions 60 vs supplied limit 50 | Supplied-field inconsistency, **not permit validation** | Emissions 40 vs supplied limit 50 |
| Calendar chronology | Delivered 2026-11-06 vs due 2026-11-04 | Late-date finding | Delivered 2026-11-02 vs due 2026-11-04 |

All 30 specific case records are in the JSON inventory, rather than duplicating drift-prone examples here.

## Acceptance criteria — required for Codex delivery

| Gate | Required evidence |
|---|---|
| FC-AC-01 — catalog completeness | All 30 stable IDs and individual use cases have valid unique IDs, exact pointers/operators/types, stage scope, source/provenance and no collisions. No unsupported rule is represented as active. |
| FC-AC-02 — detection & benign | Per rule, a failing positive and valid benign example both pass on **input and output**; preserve every original finding and genuine overlap. |
| FC-AC-03 — malformed & missing | Explicit missing left/right, malformed/duplicate-key JSON, null, Boolean, string-number, invalid ISO date, overlong/over-nested input and non-finite/excess-exponent behavior. No ambiguous silent pass. |
| FC-AC-04 — operators & precision | All 6 operators and both types tested at equality/boundary; exact numeric decimal tokens preserved without float rounding, including large integers and tiny scientific notation. |
| FC-AC-05 — stage | Input-only and output-only rules never execute on the other stage. JSON-only input vs incompatible text is represented accurately as skipped/incomplete under the applicable contract, not falsely safe. |
| FC-AC-06 — per-tenant authority | Distinct tenant configurations and `off/allow/flag/block` actions demonstrably independent, including concurrent shared Engine tests. Never add unvalidated keys to global `RULES` or bypass catalog validation. |
| FC-AC-07 — enforcement | `mode=enforce` tests assert actual recommended action, finding IDs, and stage-specific action. Observe-mode results separately asserted. All findings returned, not just the first. |
| FC-AC-08 — bounds & integrity | Remove the existing unapproved 50-comparisons-per-tenant ceiling through Director-owned schema/API coordination; use measurable bounded execution, memory, request size and latency budgets, duplicate-ID rejection, validated JSON Pointers, and existing fail-closed behavior. Add scale/performance tests above 50 and for resource exhaustion; a documented operational safety guard is not a business rule-count cap. No evaluator `eval()`, arbitrary expressions, host file access or network calls. |
| FC-AC-09 — data privacy | Finding explanations/logs omit raw compared values, JSON private keys, credentials and unrelated content; synthetic fixtures only. |
| FC-AC-10 — trusted thresholds | Distinguish submitted-field agreement from authoritative host-sourced threshold. If host injection is necessary, obtain Director API/tenant ownership acknowledgement and test anti-tampering and source provenance; do not claim external truth without it. |
| FC-AC-11 — user experience | Director-approved dashboard shows **Field comparisons** category and configurable per-tenant entries; Input/Output settings and effective actions accurate; examples and warnings reflect current saved or unsaved panel behavior. |
| FC-AC-12 — documentation | User guide: purpose, rule matrix, config examples, expected actions, limitations, how to create a tenant comparison, stages, errors and transparent noncoverage. Exact new test IDs and eval provenance linked. |
| FC-AC-13 — CI | Focused contract tests, broader regression suite, repo CI and CodeQL must pass at **exact implementation SHA**; no weakening/skipping tests or raising deadlines merely to green. Include user-facing examples and saved-eval evidence for delivered behavior. |
| FC-AC-14 — release safety | No deployment until Director/user approve production gate, exact CI, hosted tenant isolation and post-deployment verification; no paid provider or AWS cost incurred in test implementation. |

### Baseline synthetic test matrix

Current standalone `tests/test_field_comparisons_inventory.py` defines **182 expected collected cases**: 60 positive, 60 benign, 30 missing-left, 30 wrong-type and 2 inventory/scope contract tests. Verify the actual collected count from pytest rather than treating this count as executed proof. The eventual implementation should also test missing right, two simultaneous failures, `off/flag/allow/block` per tenant, parallel tenants, hostile JSON and stage scoping.

**Note on trustworthy right-hand values:** comparing `refund_amount` to a `purchase_amount` within the same untrusted JSON can detect inconsistency, but not whether either amount is genuine. Healthcare, legal, compliance and financial examples are synthetic integrity constraints only. This validator alone does not prove medical correctness, regulatory compliance, fiscal authorization, source authenticity, or fraud prevention.

## Codex implementation sequence — explicit permission gates

1. **Batch FC-1 (Codex-owned tests/docs):** verify/reconcile all 30 templates against current `cross_field` behavior; add missing-right/type/strict-JSON/operator/date/stage/simultaneous-findings regressions and saved evaluation examples. Fix bugs **only within Codex/Atlas-owned paths**; new paths preferred. Preserve current existing checks and regressions.
2. **Batch FC-2 (Director approval required):** design and review a tenant-scoped dynamic rule registry for `business.<rule_id>` with immutable tenant ownership, capacity governed by measured execution/memory/time budgets rather than a fixed arbitrary number, and strict grammar. Demonstrate no cross-tenant poisoning or unknown-ID bypass. Ask user to forward interface proposal to Director before shared-file edits.
3. **Batch FC-3 (Director-owned UI/API):** integrate tenant CRUD, accurate per-rule Input/Output effective action display, examples, controls/search and API validation. Ensure tenant does not configure arbitrary unapproved comparison pointers or host threshold values without authorization.
4. **Batch FC-4 (integration/release):** end-to-end tests, exact-head 13-job Tests and CodeQL gates, isolated release acceptance as applicable, Director sign-off and post-deploy checks.

**Communication instruction from Greg (2026-11-04):** the Director is no longer monitoring the shared coordination file. Do **not** append a Field comparisons request there. Send shared-owner changes and release requests to Greg for forwarding to the Director. GitHub artifacts and the Codex issue are the durable specification.

## Excluded from 30-rule commitment (separate backlog)

Aggregate arithmetic (sum of line items), percentages/rounding, unit and currency conversions, tolerance bands, strings/identity matching, schema-to-schema comparisons, joining external records, verifying account balances against a database, temporal sequences across requests and fairness analysis require additional checked designs and separate counts. Do not pretend these are implemented by basic `lt/le/eq/ge/gt/ne`.
