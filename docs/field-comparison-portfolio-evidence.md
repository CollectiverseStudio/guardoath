# Strict whole-portfolio generated-example audit (FC-MR2 / FC-MR4)

> **2026-11-05:** the generator defect described below is repaired in `guardoath/record_rules.py` (values solved for
> all of a record's comparisons together, each document checked with the checker's value rules, and a stable reason
> code when no document exists; tests in `tests/test_record_rule_examples.py`). The observations and the checked
> report below are from before that change; re-run `python -m tools.field_comparison_portfolio --output
> docs/evidence/field-comparison-portfolio.json` to refresh them. Contradictory and strict-self scenarios still have
> no look-alike (reported as `generator_unavailable:<code>`), so the strict audit still exits 1 by design.

This audit uses actual validated Policy objects and the real enforce-mode Engine
with one mandatory cross_field checker, on input and output. The generated
positive must report its nominated original rule ID; real overlaps stay visible.
A generated look-alike must complete with zero active findings, checker passed,
and recommended/effective Allow. A nonmatch for its nominated rule alone is
insufficient. None without an explicit generator reason is an availability gap,
not an invented feasibility explanation. No runtime source or assertions change.

```sh
python -m tools.field_comparison_portfolio --output report.json
```

**Expected current exit:1 (acceptance defects found).** The command deliberately
does not turn failed generator acceptance into a passing CI assertion. Harness
tests verify this strict classification; their passing status is not generator
acceptance. The [checked report](evidence/field-comparison-portfolio.json) records
source SHA256 and every case's stage, target/finding IDs, checker/completion status
and actions, with no generated document or submitted value. It is an observed
failing acceptance result, not a frozen desired behavior or deployed claim.

## Exact observations

| Scenario | Accepted / audited cases |
|---|---:|
| MFC-018-inventory | 60 / 120 |
| MFC-001-chain-refund | 10 / 12 |
| MFC-002-chain | 8 / 8 |
| MFC-007-equality-cycle | 12 / 12 |
| MFC-007-strict-contradiction | 4 / 8 |
| MFC-009-self-strict | 0 / 8 |

For all30 inventory templates, all60 positives include their target finding;
**none of the60 generated benign examples passes the complete portfolio**. These
are complete enforce-mode results, not checker timeouts. For example the input
refund look-alike omits or invalidates other required relationships: actual IDs
are business.deductible_not_above_claim, business.delivered_not_above_shipped and
business.temperature_above_minimum, with complete/flagged and Block/Block.

The three-rule MFC-001 scenario reproduces the nominated-refund nonmatch problem
in a smaller portfolio. Equality cycles have a feasible witness; strict cycles
and strict self-comparison cannot have one. The generator must not describe
violating documents as benign, or silently omit the reason for unavailable data.
This tool is not a feasibility solver and does not infer feasibility from a
generator return value.

## Runtime integration blocker

Reserved fix paths: guardoath/record_rules.py::_comparison_document and related
tests/test_record_rules.py contracts, plus any separately coordinated presentation
change for unavailable-with-reason. The generator currently skips an entire
companion assignment when a shared pointer is already placed, and later target
assignments can invalidate earlier relationships. Construct a joint valid witness
for feasible active portfolios, verify it through the full checker before labeling
it benign, and return a stable sanitized unavailable reason for infeasible or
unsupported portfolios. Preserve every constraint, original ID, stage action and
genuine finding. Do not suppress other rules or weaken Block to green examples.
No runtime change was made; owner acknowledgement is needed before those edits.

## MFC case-to-test acceptance map

Engine tests below are in test_field_comparisons_multirule_engine.py unless a
different file is named. Existing tests can prove manual witness/enforcement
contracts without proving generated-example correctness. Open rows remain open.

| Matrix ID | Concrete evidence | Remaining gap |
|---|---|---|
|001|test_complete_three_rule_portfolio_benign_has_zero_findings; W1 witness tests on both stages|Generated refund look-alike fails|
|002|MFC-002-chain generated audit; W3 manual benign and all-violation tests|No general DAG/API claim|
|003|30-template individual inventory tests|Dedicated shared-invoice portfolio|
|004|Individual inventory tests|Dedicated shared-prescription portfolio|
|005|test_all_operators_respect_exact_boundary_in_a_two_rule_portfolio|Combined inclusive interval|
|006|W2 strict-interval manual witness and all-original-violation tests, both stages|Generator target overwrite still invalidates companion|
|007|Equality/strict-cycle audit; W4 guarded two-cycle proof and all-violation tests|Unavailable reason/generator repair|
|008|test_equal_opposite_pointer_constraints_preserve_both_findings|Feasible eq/ne generated portfolio|
|009|Self-strict generated audit|Explicit infeasibility reason|
|010|test_nested_pointer_chain_reports_all_affected_original_rules|Generator object/leaf collision handling|
|011|test_json_pointer_escaping_with_multi_rule_overlap; W5 generated/manual escaped-pointer evidence, both stages|No broader pointer/tenant claim|
|012|test_invalid_iso_date_and_numeric_failure_report_both_original_rules|Full ordered date chain and leap-day generator|
|013|test_stage_scoping_executes_only_the_comparisons_for_that_stage|Stage-aware generated witnesses|
|014|test_shared_number_and_date_pointer_is_invalid_for_only_one_type|Generator explicit infeasibility|
|015|test_all_operators_respect_exact_boundary_in_a_two_rule_portfolio|Dedicated mixed le/ge shared-left portfolio|
|016|test_shared_engine_tenant_isolation_during_concurrent_requests|Persisted API concurrent acceptance|
|017|test_input_output_rule_settings_are_independent_for_same_original_finding; test_record_rules.py::test_tenants_sharing_an_engine_keep_their_own_comparisons_and_settings|Broader cross-tenant action combinations|
|018|All30 templates/full-Engine generated audit, both stages|60/60 benign cases currently fail|
|019|test_three_simultaneous_violations_return_all_original_rule_ids; test_invalid_secret_like_field_content_is_not_echoed_in_findings|Full30 simultaneous violations|
|020|field-comparison-scale-evidence.md and source-hashed measurements|Schema rejects >50; concurrency/RSS/API pending|
|021|test_decimal_precision_is_not_lost_when_rules_share_a_pointer|Broader exponent/resource boundaries|
|022|test_one_rule_override_does_not_change_unrelated_comparison|Contradictory portfolio plus disabled rule|
|023|test_record_rules.py::test_record_rule_ids_are_lowercase_and_cannot_name_a_static_rule|Dedicated duplicate-ID rejection evidence|
|024|test_missing_field_reports_every_affected_rule_without_suppressing_others; test_invalid_shared_left_does_not_mask_both_dependent_rules|Additional type/stage cross-products|
|025|Enforce-mode audit and existing stage/action tests|Dedicated observe-versus-enforce same benign response|

The317-case relevant group covers inventory, existing multi-rule Engine, tenant
record rules, stage settings and both evidence harnesses. No runtime/generator
fix, >50 tenant acceptance, hosted release or production readiness follows from
these passing harness/regression tests.

The [five-witness classification batch](field-comparison-witness-evidence.md)
provides exact new test identifiers for MFC001/002/006/007/011. It preserves the
source audit's actual findings and does not repair the generator or close the
other explicitly open matrix rows.
