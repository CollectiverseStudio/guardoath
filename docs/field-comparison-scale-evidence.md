# Field comparison standalone scale evidence (FC-MR3)

> **2026-11-05:** the 50-comparison limit was removed from `TenantRecord` and `Policy` (Greg's decision; the record's
> size is bounded instead: 350,000 bytes of stored settings, 262,144-byte save requests), and the example generator in
> `guardoath/record_rules.py` was rewritten (related comparisons solved together, independent groups solved once, work
> and listed text bounded per record). The observed run below, including "rejected 51/100/250/500" and the generator
> timeouts, is from before that change; re-run the command to refresh docs/evidence/field-comparison-scale.json.

This offline harness measures existing components at30,51,100,250 and500
independent numeric comparisons. It does not change tenant schemas, request
admission, runtime code, Engine deadlines, example generation or product limits.
All generated FieldComparison objects are individually validated. For component
measurement only, a plain holder supplies those objects directly to the checker
or record_rules; it is never passed to the application or Engine. Real Policy
and TenantRecord validation is separately attempted and reported without input
values. No model_construct or deployed validation bypass is used.

Run from a source checkout with its normal dependencies:

```sh
python -m tools.field_comparison_scale --samples 3 --timeout 10 --output report.json
```

Sizes and sample choices bound this benchmark, not customer rule counts. Each
component/size uses a fresh child with a10-second total wall limit, covering imports,
setup and all three samples. Timeout kills the worker and produces no invented
latency/memory measurement. In particular, a timed-out three-sample batch does
not establish that a single call took longer than10 seconds. No network, provider
or AWS calls occur. Run manually; CI only tests small cases and evidence honesty,
with no machine-dependent performance pass/fail thresholds.

## Observed run

The [machine-readable report](evidence/field-comparison-scale.json) records Python,
platform and SHA256 of the harness plus all four measured source files. Synthetic
independent JSON pointers have a joint benign witness. Each checker sample runs
that witness on both input and output and requires passed/zero findings. Generator
samples only construct and serialize the catalog; they do not prove full-portfolio
benign-example correctness (FC-MR2 remains open).

| Comparisons | Checker median ms, both stages | Generator median ms | Generator status |
|---:|---:|---:|---|
| 30 | 1.555 | 200.605 | measured |
| 51 | 2.267 | 936.060 | measured |
| 100 | 3.858 | unmeasured | timeout |
| 250 | 8.998 | unmeasured | timeout |
| 500 | 17.285 | unmeasured | timeout |

tracemalloc adds overhead. Peak traced memory excludes comparison/model setup and
is not process RSS. Output bytes are UTF-8 request JSON for checker measurements
and serialized generated catalog for generator measurements. Three observations
provide a median and a nearest-rank sample p95, not a reliable production P95.
These are reproducible observations on this machine, not capacity guarantees.

## Admission and remaining blockers

Both real schemas accepted30 comparisons and rejected51/100/250/500 at comparisons
with too_long. This documents the existing rejected implementation ceiling; it
does not endorse a50-rule product limit or claim >50 tenant support. Removing it
remains a coordinated model/API change, with measured request/time/memory/concurrency
safeguards instead of another arbitrary count ceiling. Larger direct-checker
measurements cannot substitute for persisted API, tenant actions or shared-Engine
concurrency acceptance. The generator batch timeouts are additional evidence
gaps to address before changing admission. Lazy/paginated examples require
separate runtime ownership and acceptance.

Acceptance mapping: MFC-020/FC-AC-08 have partial standalone measurement evidence,
not completed tenant-scale acceptance. FC-MR2/MFC-018 and contradictory-portfolio
feasibility remain open; do not label a generated look-alike globally benign
merely because its nominated rule does not fire. Existing independent multi-rule
Engine coverage remains in test_field_comparisons_multirule_engine.py.

Seven harness tests cover both-stage51-rule violation completeness and original
IDs, complete benign measurement, real schema reporting, generated output scope,
timeout evidence, sanitized child errors and a real bounded child. No security
assertion, privacy budget, fail-closed behavior or test timeout was relaxed.


## Current release-gate contract (2026-11-07; supersedes historical acceptance limitations)

The **Observed run** above is historical 2026-11-05 evidence from before the tenant/model 50-comparison cap was removed. It must not be cited as the current live product ceiling or the current performance benchmark. Likewise, the archived `docs/evidence/field-comparisons-current-source-acceptance.json` is tied to its own recorded prior commit, not automatically to the latest `main`.

The dedicated `.github/workflows/field-comparison-evidence.yml` gate now executes **three independent checks at the same exact triggering GitHub SHA**, rather than accepting component measurements alone:

1. **Strict portfolio:** `python -m tools.field_comparison_portfolio` must verify complete Input/Output witness findings, distinguish proven unsatisfiable combinations from merely unavailable examples, and refuse unknown/incomplete results.
2. **Bounded component scale:** `python -m tools.field_comparison_scale --samples 3 --timeout 10` must establish truthful 30/51/100/250/500 measurements without weakening resource limits or treating timeouts as passes.
3. **Actual authenticated integration scale:** `python -m pytest -q tests/test_field_comparison_large_tenant_api.py` must save and reload tenant-owned 30/51/100/250/500 comparisons, evaluate both stages through the shared Engine, enforce stage actions, return every relevant violation, isolate tenants and reject stale revisions. The fresh JUnit XML accompanies the synthetic portfolio/scale reports.

An error in **any** of the three gates marks the workflow as failed, while preserving the available reports as a 14-day GitHub Actions artifact with the exact source SHA and per-gate status. This scoped workflow now triggers on relevant Engine, tenant, API, dashboard wiring, ordinary-comparison editor and 500-rule browser acceptance changes on `main`; it is not a CI outcome by itself. The normal Tests browser job must separately pass the actual dashboard workflows; CodeQL, the production source parity check and 15 live verification checks remain mandatory. Do not promote an old artifact or a green component benchmark into exact-head tenant/browser release acceptance.
