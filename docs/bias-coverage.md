# Bias group: local matched-decision consistency

Version 0.22.0. `matched_outcomes` is a real local structured comparison. The
older `bias` adapter remains a synthetic test double. Neither is a general
free-text bias classifier or certification of fairness.

## Review criterion

Given explicitly configured attributes and submitted decision factors, compare
records whose declared factors match exactly and whose attributes differ. Flag
different submitted outcomes for review. Every record must have a comparable
counterpart; otherwise the entire assessment is incomplete. This cannot prove
that omitted real-world factors were equal, that the selected factors are
appropriate, that records are authentic, or that an attribute caused an outcome.
Equal outcomes also do not prove fairness: they can be equally inappropriate.

This is a configurable consistency test, not demographic parity, equalized odds,
a causal counterfactual model or a statistical significance test. Repeated rows
are compared but are not independent observations. There is no confidence score,
training dataset, judge-model call or automatic extraction of decisions/attributes
from prose. The caller collects/structures the compared scenarios.

## Contract

A policy configures `matched_outcomes` and a same-named checker rule together:

```json
{
  "matched_outcomes": {
    "attributes": ["group"],
    "outcome_type": "string"
  },
  "rules": [{"checker_id": "matched_outcomes", "on_finding": "flag"}]
}
```

Attributes are one to eight distinct ASCII identifier names, with no duplicates
under case folding. Each scenario's attributes object must have exactly those
keys. Values are nonblank strings of at most 100 characters. The test does not
infer which attributes are demographic, legally protected or relevant. Review
that configuration with the application's stakeholders.

Content format must be JSON, with exactly one envelope field `scenarios`:

```json
{
  "scenarios": [
    {"factors": {"qualification": "same"}, "attributes": {"group": "A"}, "decision": "approve"},
    {"factors": {"qualification": "same"}, "attributes": {"group": "B"}, "decision": "deny"}
  ]
}
```

There must be 2–20 records. Each has exactly factors, attributes and decision.
Factors must be a nonempty object with at most 20 immediate keys. Configured
attribute names may not reappear as factor keys at any nested level, under case
folding. This catches an explicit overlap, not undeclared proxies or encoded
attributes. Objects compare independently of key order; arrays retain their
order; strings are exact/case-sensitive. Boolean, null, number and string types
remain distinct. There is no whitespace, Unicode or semantic normalization.
Numeric values compare exactly from raw JSON decimal tokens; numerically equal
integer/decimal forms and signed zero are equivalent. Boolean true is not 1.
Existing JSON byte/depth/container/number guards and the numeric exponent bound
apply. No new dependency or network/model download is introduced.

Decision type is explicitly string, boolean or number. Strings must be nonblank,
at most 1,000 characters; booleans are strict; numbers are finite supported JSON
numbers, without string/Boolean coercion or score tolerances. String decisions
like Approve and approve differ. Normalizing application-specific decision
labels is the caller's responsibility before using this exact comparison.

## Results and privacy

- Comparable outcomes equal: completed local pass for this submitted comparison.
- Comparable outcomes differ: `bias.matched_outcome_difference`, medium review
  severity, with safe counts of differing/comparable pairs and a fixed explanation.
- Malformed/missing/extra/overbudget fields: error
  `matched_outcomes_invalid_records`; required assessment incomplete.
- Different factors, no attribute contrast or any uncovered record:
  `matched_outcomes_insufficient_comparisons`; incomplete, no partial findings
  released as a completed assessment.
- Text/non-JSON content: skipped `matched_outcomes_json_required`; incomplete
  when required. A demographic mention alone is not classified as biased.

Findings/reports do not expose attribute names/values, decisions, factor fields,
raw content or request_context. Request context is not examined. The complete
body exists transiently on the local server; this is not an anonymization tool.
Selected PII/secrets checks are separate if an application needs them.

The standalone sample profiles flag input differences and recommend blocking
output differences. Observe mode keeps effective allow; the dashboard holds
findings/incomplete results independently. Enforce variants apply the configured
actions. Required errors use failure_action. An optional external checker is
withheld when this selected local checker flags/errors, even on_finding=allow.
This comparison by itself is not a sufficient privacy gate for external traffic.

## Run and inspect the complete batch

```powershell
py -3.13 start_dashboard.py --evaluate --suite bias
```

Or:

```bash
python -m guardoath.evaluate --cases evals/bias.json --policies policies/bias-batch.json
python -m pytest tests/test_matched_outcomes.py -q
```

The saved regression suite contains 50 scenario definitions, each run on input
and output: 100 expectations. Fifty intentionally expect incomplete assessments.
There are no simulated cases or provider calls. Additional tests cover policy
validation, numeric precision, multi-attribute changes, safe API responses,
external gating, full pair coverage and noncomparable records. Browser tests
exercise independent examples, held findings, evidence gaps and standalone reports.

These synthetic regression definitions are not an independently reviewed fairness
benchmark, a tuning/holdout split or evidence of live-model accuracy. Semantic
bias rubrics, reviewed populations/proxy choices, matched model trials, context
adequacy, statistical analysis and reviewer feedback remain separate work.

## Design context

Fairlearn's primary documentation discusses why measurement choices and social
context matter to fairness assessment. It informs these explicit limits; the
Fairlearn package is not installed or claimed as the implementation of this
exact local comparison:
https://fairlearn.org/main/user_guide/fairness_in_machine_learning.html.
