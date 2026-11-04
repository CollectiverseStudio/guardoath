# Field comparisons — complete multi-rule regression acceptance matrix

**Requested by Greg:** 2026-11-04. **Status:** implementation request for Codex; no direct Codex worker control is asserted. **Source:** [Issue #28](https://github.com/CollectiverseStudio/guardoath/issues/28). **Owner boundary:** new evidence/tests/docs are available; `guardoath/record_rules.py`, shared Engine/tenant/API and dashboard changes require Greg-relayed Director approval. Do not use the Director coordination file as the handoff channel.

## Definition of a benign example

For a tenant with active field-comparison rules on the requested stage, a generated `look_alike` is **benign** only if the **entire cross_field checker** returns `passed`, with **no findings from any active comparison** and no incomplete/errors. Under a policy containing only this mandatory checker and configured for `mode="enforce"`, a truly benign document must recommend **Allow**, not an error-derived Block or Flag. A negative labeled benign is not accepted merely because its nominated rule does not fire.

A generated positive example must return the intended `business.<rule_id>` finding. Genuine overlaps must remain visible; do not artificially suppress other findings to make a one-rule assertion pass. If a set of comparisons is contradictory or a feasible benign document cannot be constructed without changing constraints, **do not produce a fake benign example**: mark it unavailable with a stable, sanitized reason and provide a separately tested inconsistency example.

## Concrete deterministic regression cases

| ID | Comparison portfolio | Expected complete-benign acceptance | Failure target and hazards |
|---|---|---|---|
| MFC-001 | shipped ≤ ordered; delivered ≤ shipped; refund ≤ purchase | Refund look-alike must include all five necessary values and yield no findings | Regression for omitted `delivered_qty` |
| MFC-002 | requested ≤ stock; stock ≤ warehouse | All shared-pointer relationships hold | Failure in first relationship must not hide second |
| MFC-003 | paid ≤ invoice; deductible ≤ paid; refund ≤ invoice | Full portfolio passes for generated benign samples | Shared right pointer `invoice` |
| MFC-004 | proposed ≤ prescribed; administered ≤ prescribed | Both comparison rules report correctly without suggesting clinical approval | Shared right pointer |
| MFC-005 | min_temperature ≤ observed; observed ≤ max_temperature | Inclusive valid boundary passes | Equal and out-of-range boundaries |
| MFC-006 | observed > minimum; observed < maximum | Both strict bounds pass only in nonempty interval | At equality both strict failures preserved |
| MFC-007 | A ≤ B; B ≤ C; C ≤ A | Equality is a feasible benign assignment | Strict variant `A < B; B < A` is infeasible |
| MFC-008 | A = B; B ≠ C | Feasible if C differs | Infeasible combination `A = B; A ≠ B` must be surfaced |
| MFC-009 | A ≤ A and A < A | First is always true, second impossible | Explicit identical-pointer conflict; no fabricated benign example |
| MFC-010 | `/outer/a` ≤ `/outer/b`; `/outer/b` ≤ `/outer/c` | All nested paths present | Parent object / leaf type collisions |
| MFC-011 | `/a~1b` ≤ `/c~0d` with companion rule | Escaped pointers handled correctly | Literal `/` and `~` tokens |
| MFC-012 | date_start ≤ date_end; date_end ≤ date_expiry | ISO dates in order | Invalid dates, leap day, missing keys |
| MFC-013 | input-only A ≤ B; output-only B ≤ C | Input ignores output-only rule; output ignores input-only | No cross-stage false positives |
| MFC-014 | A ≤ B numeric; B ≤ C date | Cannot bind B to mutually incompatible types on one stage | Return unavailable for a common benign sample, not a silent success |
| MFC-015 | A ≤ B and A ≥ C | Feasible when C ≤ B | Both original rule IDs preserved on failure |
| MFC-016 | Two tenants with identical rule IDs and different pointers/actions | Each tenant sees only its own configuration and results | Shared Engine and concurrent requests |
| MFC-017 | Same rule action Block in tenant A / Off in tenant B | Tenant A enforces Block and B suppresses own finding | No cross-tenant rule-action leakage |
| MFC-018 | 30 inventory templates on one record | For each generated look-alike, either zero active comparison findings or explicit unavailable | Existing tests that check only target ID are insufficient |
| MFC-019 | 30 active templates, multiple invalid values | All applicable findings returned (not first only) | No raw field values, private keys or credentials in explanations |
| MFC-020 | 51, 100, 250 and 500 comparisons | No arbitrary tenant-level 50-rule rejection after approved schema change | Measure request bounds, creation latency/P95, memory and concurrent operation |
| MFC-021 | Numeric comparison with high-precision decimal and large integer | No binary-float rounding or implicit string coercion | Extreme exponent/invalid JSON fail closed |
| MFC-022 | Tenant with conflicting comparisons and disabled rule override | Disabled rule is not enforced; active constraints remain authoritative | Do not silently discard conflicts from other enabled rules |
| MFC-023 | Same rule ID in two comparison declarations | Explicit configuration validation failure | Duplicate IDs not collapsed |
| MFC-024 | A ≤ B with missing B, Boolean A, null B and string-number A | No silent defaulting or coercion | Each case reports the correct missing/invalid finding |
| MFC-025 | Complete benign response in `mode="observe"` vs `mode="enforce"` | Status and recommendation reflect configured mode | Do not equate observe with real enforcement |

## Required test structure

- **Generator-level:** `record_rules(record)` must return stable, deterministic documents and accurately label `example` vs `look_alike`; no shared pointer may disappear because an earlier trial assignment failed. Conflicting constraints must be represented as unavailable with a reason instead of fictitious benign data.
- **Engine-level:** deserialize each generated example; run `Engine.check(..., Stage.INPUT/OUTPUT)` for the same tenant's *complete* configuration. Validate **all findings, evaluation status, effective action and source rule IDs**. Run with `mode="enforce"` for enforcement assertions.
- **Tenant/API-level:** test persisted tenant A/B configurations, per-rule actions and stage boundaries with a single shared Engine. Dynamic `business.*` rules belong only to their tenant, never the global catalog.
- **Scale-level:** do not claim >50 supported until the Director-owned `TenantRecord` and `Policy` limits and action-map ceilings are changed/accepted. Benchmarks must not rely on external providers, paid APIs or AWS writes.
- **User experience:** dashboard may only describe a sample as benign if it passed the complete comparison portfolio; if no such sample is computable, show an accurate unavailable explanation.

## Exact deliverables and release gates

Codex should add a dedicated parametrized test module, cover all 25 case families (and cross-stage variants), retain evidence of full 30-template regression runs, and provide an acceptance report listing **test ID, rule IDs, expected vs actual, commit SHA and CI run IDs**. Add the user-guide limitations and report any failing case before attempting runtime changes requiring shared-file authorization. Required gate for merged implementation is exact-SHA **13/13 Tests and 3/3 CodeQL**, with no skipped/weak tests or hidden overlaps. Production deployment remains separately authorized.

**Important:** this matrix defines the requested acceptance behavior and is not proof that any of these test cases have run or passed.
