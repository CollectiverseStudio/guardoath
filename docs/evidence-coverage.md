# Accuracy group: local citation consistency

Version 0.22.0. This is a real deterministic citation check, not a fact checker,
semantic entailment model or guarantee that every claim has supporting evidence.

## Trusted catalog and content contract

A versioned policy configures `evidence.sources`, with 1–20 unique, case-sensitive
source IDs and nonblank source text up to 8,000 characters each. IDs use ASCII
letters/digits/underscore/dot/hyphen, length 1–80. A required `evidence` rule and
its configuration must be supplied together. Source text is copied into the
engine's startup snapshot, is never fetched remotely and is not exposed in
application metadata or finding messages. The application owner must establish
source authenticity, currency, relevance and appropriate use.

The JSON content has exactly `answer` and `citations`. Answer is a nonblank string
of at most 8,000 characters. Citations is an array of at most 20 objects, each
with exactly `source_id` and a nonblank `quote` of at most 2,000 characters. A
caller cannot add or replace the policy catalog through the request. Ordinary
text is skipped and a required check is incomplete, not an accuracy pass.

Inline markers are `[[source_id]]`, with at most 20 occurrences. Repeated markers
can refer to a single citation entry. This first contract permits one quotation
per source. Each referenced ID needs a citation entry; entries need a marker;
both must name configured sources. Quotes must appear exactly in the configured
source. No case/whitespace/Unicode normalization is applied. A minimum trimmed
quote length (default 20, configurable 1–1,000) avoids padding-only length claims;
it does not establish that the quote is relevant or supports the answer.

## Findings and errors

| Rule | Checked condition |
| --- | --- |
| evidence.missing_citation | No inline markers or no citation entries |
| evidence.invalid_marker | Unmatched/unsupported double-bracket marker syntax |
| evidence.unknown_source | Marker or entry outside the trusted catalog |
| evidence.missing_reference | Marker has no corresponding entry |
| evidence.unused_citation | Entry is not used by any marker |
| evidence.quote_mismatch | Quote is not an exact source substring |
| evidence.quote_too_short | Trimmed quote below configured minimum |
| evidence.duplicate_citation | Multiple entries for the same source |

Findings are deduplicated by rule and use generic medium-severity review messages.
They never include answer text, quote text, source IDs/bodies or validator errors.
Malformed JSON/shape/type/size errors return `evidence_invalid_document`;
more than 20 markers returns `evidence_reference_limit`; wrong content format
returns `evidence_json_required` (skipped). Required errors/skips are incomplete.
The sample observes input flag/output block recommendations, with effective allow;
the dashboard holds findings/incomplete results. Enforce variants apply actions.

The explicit unrelated-claim example passes this limited check while displaying
its limit: an answer about the Moon can attach a valid return-policy quote. This
checks source/citation consistency only, not factual truth, hallucination,
source-to-claim entailment, coverage of every assertion, plagiarism, legal
adequacy, current web availability or academic citation/database existence.

## Complete batch

```powershell
py -3.13 start_dashboard.py --evaluate --suite evidence
```

Or:

```bash
python -m guardoath.evaluate --cases evals/evidence.json --policies policies/evidence-batch.json
python -m pytest tests/test_evidence.py -q
```

The 50 scenario definitions run on input and output: 100 expectations, including
36 deliberate incomplete evaluations. Positive cases, every finding family,
trusted-catalog substitution attempts, marker/quote limits, malformed documents,
stage/mode decisions and the explicit entailment gap are covered. Additional
API tests check sanitized responses/metadata and no network access. Browser
examples are independent per editor and include the unassessed-claim example.
These are software regression tests, not an independent factual accuracy dataset.
No provider, model download or dependency is added.
