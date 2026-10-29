# Additional use cases 51–100 and acceptance tests

Greg's second list is preserved below, numbered 51–100 to keep the first list's
IDs stable. A use case is a product requirement, not proof that a detector exists.

**Current acceptance coverage:** 21 examples have scoped/partial controls with
runnable tests; 29 require new components and reviewed fixtures. The initial 77 tests
in [test_additional_use_cases.py](../tests/test_additional_use_cases.py) exercise
the previously supported controls through actual API/engine calls. The subsequent
433 tests in [test_data_quality.py](../tests/test_data_quality.py) cover the four
new local data contracts and their API/configuration boundaries. These do not claim to
implement the missing semantic, behavioral, industrial or cryptographic services.
No skipped placeholder test is presented as a passing detector. All planned rows
below specify an invalid/attack case, a legitimate control, and an edge/failure
case that a future implementation must satisfy before being marked complete.

Existing test-family names contain the stable IDs, such as `test_uc092_...`.
Input/output stages are both exercised where the contract applies. Transport,
quota and policy controls are tested at their actual layer. Some proposed
features belong in an integrating application or specialized preprocessing layer,
not in a text-content checker. User-agent strings, client timestamps, inferred
human behavior and caller-supplied hashes are not automatically trusted evidence.

## Semantic integrity and context drift

| ID | Requested use case | Current status / executable portion | Required acceptance tests for full coverage |
| --- | --- | --- | --- |
| UC051 | Topic hijacking detection | Planned; no semantic topic evaluator | Flag a reviewed HR-to-stock-tip diversion; allow legitimate compensation/retirement-benefit questions; return incomplete when the approved topic policy/context is missing. Requires a scoped rubric and reviewed model/provider evaluation. |
| UC052 | Sarcasm/irony disambiguation | Planned; no severity-intent classifier | Distinguish reviewed sarcastic praise from genuine praise and literal bug reports; test dialect/cultural variation and ambiguity; route uncertain cases to review rather than inventing intent. |
| UC053 | Multilingual code switching | Partial: Unicode transport preserved, `uc053` | Current test verifies mixed-language text arrives unchanged, not semantic safety. Future: equivalent reviewed safe/unsafe meanings across language mixtures, quoted speech, unsupported-language and uncertain-translation handling. An en API field is not language detection. |
| UC054 | Context-window saturation flooding | Partial: character/byte caps, `uc054_and_uc097` | Current: accept the exact character boundary, reject over-limit bodies before checking and never silently truncate. Future: model-specific token budgets, repetitive sub-limit floods and meaning-preserving handling. No current tokenization/context-window guarantee. |
| UC055 | Logical contradictions | Planned; no premise/logic model | Review contradictory premises under an approved rule; allow quotations and questions about paradoxes; test missing assumptions and avoid claiming infinite reasoning has been prevented. |
| UC056 | Conversational loops | Planned; no server conversation ledger | Detect repeated equivalent turns under a configured session budget; allow legitimate follow-up questions; handle concurrent, expired and missing session history without a false pass. |
| UC057 | Persona hijacking | Planned semantic persona check; optional input attack service is narrower and uncalibrated | Flag reviewed attempts to override an approved persona; allow authorized role changes/quoted instructions; test unavailable persona policy and provider uncertainty. Do not infer legal authority from role language. |
| UC058 | Stale-context eviction | Planned; no session freshness engine | Mark references beyond a trusted freshness threshold; allow current/versioned references; test clock skew, missing source timestamps and concurrent updates. Do not delete conversational data silently. |
| UC059 | Idiomatic threats | Planned contextual classifier | Detect reviewed culturally specific threats; allow benign figurative speech and threat reporting; test dialect coverage and uncertainty with independent reviewer labels. Keyword hits alone are insufficient. |
| UC060 | Compressed-prompt reconstruction | Planned beyond the documented single labeled Base64/hex layer | Test a supported, versioned compression/shorthand format; preserve legitimate compact text; reject malformed/overbudget reconstruction and verify meaning before any transformation. No arbitrary LLM-speak decoder exists. |

## Behavioral and bot defenses

| ID | Requested use case | Current status / executable portion | Required acceptance tests for full coverage |
| --- | --- | --- | --- |
| UC061 | Keystroke dynamics | Planned; the API does not collect typing events | Test reviewed human/automation telemetry, paste/accessibility workflows and missing/spoofed events; define privacy-reviewed collection and uncertainty. Typing cadence must not become proof of identity. |
| UC062 | Prompt-frequency anomalies | Partial: per-application quota, `uc062` | Current: two keys for one app share a quota across stages; another app is independent; overload has Retry-After. Future: per-user/session anomaly baselines, benign bursts, account correlation and distributed counters. No behavioral attack classifier exists. |
| UC063 | Copied-payload fingerprinting | Planned; no live adversarial corpus | Test known-version payloads, benign similar text and stale/unavailable corpus behavior; define normalization, corpus provenance, privacy and false-positive review. |
| UC064 | User-agent/context matching | Planned; no device/behavior model | Test verified telemetry mismatches and legitimate low-capability/accessibility clients; test spoofed headers and absent signals; never use a user-agent string alone as authentication or bot proof. |
| UC065 | Adversarial audio filtering | Planned; no audio ingestion/DSP pipeline | Test approved synthetic adversarial audio and benign audible commands; verify sample-rate/format bounds, clipping and unsupported inputs; measure preserved command meaning, not just filtering success. |
| UC066 | Session takeover signals | Planned; no session-risk ledger | Test reviewed abrupt changes, legitimate language/tone changes, shared devices and missing history; require independent authentication/response logic rather than tone-based identity claims. |
| UC067 | Adversarial image perturbations | Planned; no image preprocessing | Use approved adversarial/benign image fixtures; measure model behavior and image quality after a candidate transform; test corruption and resource limits. Smoothing/compression cannot be assumed to neutralize every attack. |
| UC068 | Coordinate-spam blocking | Partial: declared array limits, `uc068` | Current: an illustrative schema accepts up to three coordinate pairs and rejects four. Future: application-approved limits, pair/CRS validity, downstream workload/load limits and no silent truncation. No routing calculator is invoked. |
| UC069 | Audio CAPTCHA response | Planned; no audio/challenge service | Bind verified challenge IDs to responses; test correct/incorrect speech, accents/accessibility, expiry/replay and unavailable transcription. No bypassing or solving third-party CAPTCHA systems. |
| UC070 | Distributed attack correlation | Planned; no shared event store | Test approved coordinated synthetic sequences, unrelated benign accounts and cross-worker/outage behavior; define tenant separation, privacy, retention and incident-review thresholds. |

## Advanced data and schemas

| ID | Requested use case | Current status / executable portion | Required acceptance tests for full coverage |
| --- | --- | --- | --- |
| UC071 | Embedding-distance thresholding | Planned; no embedding model/index | Calibrate reviewed near/far examples and legitimate lookalikes; test zero vectors, dimensions, model/index version mismatch and outages. Cosine proximity is not proof of malicious intent. |
| UC072 | Dynamic JSON Schema alignment | Scoped: versioned schemas, `uc072` | Current: v1/v2 enforce different contracts and preserve active snapshots; good/bad documents are checked on both stages. Bounded parsing precedes schema validation. Live authoring/publication, remote schemas and before-tokenization semantic safety are not implemented. |
| UC073 | Statistical data-drift fencing | Planned; no baseline/distribution evaluator | Test reviewed in/out-of-distribution samples, seasonality and benign population changes; reject missing/version-mismatched baselines and inadequate sample sizes; define reviewed thresholds and uncertainty. |
| UC074 | Missing-feature quotas | Scoped: declared null quota, `uc074` | Current: fixed-length feature arrays allow zero/one null and reject two/four under a supplied schema, without modifying values. Future application work: choosing the quota, repair workflow and validated imputation; no imputation is performed. |
| UC075 | Graph-edge validation | Partial: declared directed graph structure, `graph` | Checks cycles, self-links, duplicate edges, unknown endpoints and bounds. Relationship legal validity remains unassessed. |
| UC076 | Timestamp sequence alignment | Partial: within-packet timestamp order, `timestamps` | Checks explicit-offset instants, ties policy, malformed values and caps; no cross-request state, freshness, sorting or repair. |
| UC077 | SQL parameter injection defense | Partial: proposed-tool allowlist, `uc077` | Current: execute_sql is rejected because it is not allowed, regardless of query content. This is not SQL sanitization. Future integration tests require the actual parameterized query builder/driver, identifier allowlists, least privilege and legitimate punctuation controls; do not treat prompt stripping/escaping as sufficient. |
| UC078 | Cross-field logical consistency | Partial: declared numeric comparisons, `uc078` | Current: years of experience is compared with submitted age. Birth-year/current-date derivation, chronology, missing/contradictory age claims and authoritative context are not verified. Add reviewed derivations before claiming the original DOB example is fully checked. |
| UC079 | Categorical outlier quarantine | Scoped: exact taxonomy codes, `uc079` | Current: allowlisted category codes pass; other/case-variant/empty codes fail. This does not semantically classify arbitrary prose or infer an industry taxonomy. Future quarantine/reviewer routing needs separate integration. |
| UC080 | Deterministic rule interception | Scoped: policy-file conditional schemas, `uc080` | Current: a business customer requires company_name; an individual does not. Both stages enforce the selected schema in the API; the integrating application controls when it calls a model. A no-code UI, live updates and arbitrary business-expression execution are not implemented. |

## Specialized formats and industrial uses

| ID | Requested use case | Current status / executable portion | Required acceptance tests for full coverage |
| --- | --- | --- | --- |
| UC081 | DICOM identity scrubbing | Planned; no binary medical-image reader | Use synthetic identifiable/de-identified fixtures; verify the approved metadata and image-content scope, private/unknown elements and malformed/large files; withhold incomplete transformations. Removing a few headers must not be reported as complete de-identification. |
| UC082 | CAD geometry validation | Planned; no CAD/mesh reader | Test approved closed/open/non-manifold geometry, legitimate assemblies and corrupt/oversized files; define units, format support and physical checks. Parser success alone cannot establish print/manufacturing safety. |
| UC083 | Source-code AST validation | Planned; current tool-name/argument checks do not parse source code | Test language/version-specific syntax and reviewed forbidden constructs, legitimate examples and parser/resource failures. AST parsing does not prove absence of illegal behavior or memory leaks; execution needs an independent sandbox/authorization boundary. |
| UC084 | DNA/RNA sequence screening | Planned; specialized reviewed workflow required | Define the supported sequence/order contract, trusted reference/version and qualified review process; use authorized non-sensitive synthetic fixtures, benign controls and missing-reference/error cases. No pathogen/toxin sequences, screening corpus or clearance service are added here. |
| UC085 | Sensor extremum filtering | Partial: declared scalar bounds, `uc085` | Current: illustrative 0–120 F bounds accept endpoints and reject 500, negative and Boolean readings. These are application limits, not a universal physical model. Temporal spikes, calibrated sensor faults and smoothing are not assessed or modified. |
| UC086 | Patent claim structure | Planned filing-specific validator; existing schemas enforce generic fields only | Define the selected filing format/version and reviewed structural rules; test valid/invalid claims, dependencies, missing sections and unsupported versions. Generic JSON validity is not regulatory acceptance or legal adequacy. |
| UC087 | Geospatial boundaries | Partial: coordinate bounding box, `uc087` | Current: inclusive illustrative lat/lon bounds are tested. Polygon holes/edges, CRS, antimeridian, altitude, timestamps and navigation authorization are not implemented. Test those explicitly with approved geometry before autonomous use. |
| UC088 | Financial ledger balancing | Partial: exact line-item sums and declared currency, `ledger` | Sums bounded amount strings in integer minor units with explicit scale/currency; aggregate comparison remains available. No accounting correctness or fraud claim. |
| UC089 | Telematics smoothing | Planned; no time-series filter | Test approved speed spikes, legitimate rapid changes and missing/out-of-order telemetry; measure distortion and bounded latency; never substitute smoothing success for driver-risk or sensor-truth validation. |
| UC090 | Audio rate/format alignment | Planned; no audio decoder/resampler | Test supported mono/rate/container inputs, valid conversions and malformed/oversized/unsupported media; verify sample counts, duration and meaning/quality preservation. No automatic 16 kHz conversion is present. |

## Defensive and output-aided input validation

| ID | Requested use case | Current status / executable portion | Required acceptance tests for full coverage |
| --- | --- | --- | --- |
| UC091 | URL threat-intelligence lookup | Planned; no URL extraction/reputation service | Test approved malicious/benign fixtures, redirects, unavailable/stale intel and private/network-local destinations. Define an SSRF-safe lookup design, privacy, cost and permitted egress; never fetch arbitrary submitted URLs by default. |
| UC092 | API over-posting | Scoped: extra-field rejection, `uc092` | Current: attempted mode/rule/schema/failure-action/egress overrides receive sanitized 422 responses and leave the active policy unchanged. Credentials still bind applications. Nested application fields need their own additionalProperties rules; tenant/role authorization is separate. |
| UC093 | Prompt dependency graphs | Partial: supplied directed graph, `graph` | Checks declared graph cycles/references/bounds; no extraction from prose, package version resolution or execution. |
| UC094 | Hallucination-prone ambiguity | Planned; no reviewed ambiguity/clarification evaluator | Test reviewed ambiguous prompts, adequately specified controls and cases requiring extra context; define uncertainty and clarification behavior. Do not promise that wording statistically predicts hallucination without independent evidence. |
| UC095 | Trusted SHA-256 verification | Partial: bounded submitted bytes and policy SHA-256, `integrity` | Checks exact UTF-8 or decoded Base64 bytes against trusted server digest. No uploads/streaming, provenance or poisoning assurance. |
| UC096 | Sanctions/PEP matching | Planned; no regulated-entity list or entity resolver | Use approved reference versions and synthetic names; test matches, legitimate namesakes, transliteration, incomplete/stale lists and uncertain identities. Name similarity is not proof of identity, legal restriction or clearance. |
| UC097 | Prompt redundancy stripping | Partial: hard limits, `uc054_and_uc097` | Current: inputs are preserved within bounds and rejected over limits; no silent rewriting. Future normalization tests need idempotence, preserved code/quotes/meaning, adversarial nesting and bounded output; no stripping/truncation is currently performed. |
| UC098 | Token-to-byte mismatches | Partial: UTF-8 envelope rejection, `uc098` | Current: malformed UTF-8 is rejected before a checker. No model tokenizer is installed and no token/byte correspondence is verified. Future tests must pin a tokenizer, cover multibyte/combining text and reject invalid/unsupported encodings without claiming universal crash prevention. |
| UC099 | Organization phrase blocklists | Partial: versioned configured terms, `uc099` | Current: literal terms are checked with the existing normalization; different policy versions retain their own terms. No instantly updated directory or live policy publisher exists. Future tests need authorized publication, version/cache consistency and outage behavior; phrases are review signals, not semantic ownership judgments. |
| UC100 | Zero-knowledge input proofs | Planned; no proof system/verifier | Specify the exact circuit, statement, trusted public inputs and supported verifier; test valid/invalid/tampered proofs, replay, setup/version mismatch and malformed bounds. Do not invent cryptography or infer plaintext safety from a structural proof. |

## Execution, reporting and completion rules

Run the new tests in a prepared development environment:

```powershell
py -3.13 -m pip install -e ".[test]"
py -3.13 -m pytest tests/test_additional_use_cases.py -q
```

These **77 unit/API/engine assertions** are collected by the normal CI pytest
jobs. They are separate from the 15 saved local regression suites (1,430
expectations) opened by `start_dashboard.py --evaluate --suite all`; no 29 planned
features have been added to that index as fake passes. Existing broader tests
remain relevant: ingress/quotas, schema contracts, business comparisons, secret
terms, proposed tools, privacy and provider-gate tests.

For every future row, completion requires implementation, positive and legitimate
negative controls, malformed/unsupported and resource/provider failure cases,
privacy-safe machine-readable results, stage/mode decisions, integration tests,
examples and an updated guide. Models/datasets require independent reviewed
accuracy and uncertainty evaluation; protocol fixtures alone are insufficient.
Transformations require preserved semantics and full revalidation. API errors,
missing required evidence, unavailable services and unsupported required inputs
must not silently become successful assessments. No paid service or deployment
has been activated for this catalog.

## Primary engineering references

These inform test boundaries, not a certification claim or an implemented scanner:

- https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html
- https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html
- https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html

The subsequent [data-quality batch](data-quality-coverage.md) adds four local checkers, 400 saved regressions and API/browser coverage. See that document for precise contracts and remaining gaps.
