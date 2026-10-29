# Valinator implementation specification

Version 0.1, October 27, 2026. This repository document captures the initial
engineering baseline. The separate product specification covers the wider plan.

## Purpose

Check AI inputs and outputs against application-specific, customer-approved
policies. Reuse existing checkers behind interchangeable adapters. Add consistent
decisions, contextual bias rubrics, reviewer workflows, and regression evidence.

## Initial scope

- English text for customer-support applications.
- Input and output API endpoints, with application-scoped credentials.
- Required-field, type, permitted-value, and size validation.
- Versioned startup policies and an explicit decision order: block, flag, allow.
- Observe mode separates recommended action from effective action.
- Errors, timeouts, skipped checks, and unsupported required checks produce an
  incomplete assessment, never an implicit successful check.
- No raw prompt or response retention by default.

## Adapter interface

An async checker accepts a request, trusted policy, and stage. It returns checker
identity and version, status, findings, and a simulation label. The engine supplies
the deadline and records latency. Provider errors must not expose exception text,
source content, or credentials. Scores from unrelated checkers must not be averaged
into one confidence number. External data routing must be approved before adding
live adapters. Local-only prototype cost fields must change when live providers
are introduced.

## Planned component evaluation

Use Pydantic and JSON Schema for deterministic checks. Evaluate Presidio for local
personal information handling, Bedrock ApplyGuardrail as a first managed safety
adapter, OpenAI Moderation as an optional alternative, and Promptfoo for regression
evaluation. NeMo, selected Guardrails AI Hub validators, Azure Prompt Shields,
Model Armor, Instructor, Outlines, and Garak remain candidates where requirements
justify them. LLM Guard was archived when checked; do not make it the main
dependency without a maintenance plan. Check licenses of packages and model
weights separately before adoption.

The optional local PII adapter reuses Presidio's email, credit-card, and U.S. SSN patterns
and validators without invoking its NLP engine. It scans decoded JSON as well as
text, uses an offline domain snapshot, and returns generic entity findings without
source values. The current scope excludes names, addresses, phone numbers, and
full PHI classification. Missing required adapters remain incomplete assessments.
Version 0.5 adds policy-controlled redaction, a separate transformation result,
and full active-check revalidation before returning transformed content. Observe
mode returns a preview only; enforcement can recommend using rechecked content.
Schema failures, sensitive JSON keys, and uncompleted or simulated rechecks
withhold transformed content. Redaction does not establish full anonymization.

Version 0.6 adds a local secret checker using selected detect-secrets provider and
private-key patterns under bounded regex execution. It inspects canonical text
and decoded JSON without credential verification, baseline files, or caller
suppression directives. Coverage is limited to supported patterns, not arbitrary
passwords, proprietary documents, or every current provider key format. The
example policy flags inputs and recommends blocking outputs in observe mode.

## Bias evaluation

Define reviewed rubrics for stereotypes, unsupported demographic assumptions,
and unequal treatment of equivalent support requests. Single-response concerns
need contextual evidence and uncertainty. Repeated matched scenarios are needed
to investigate group-level patterns. A mention of a demographic attribute is not
automatically a problem. Legitimate accessibility differences, quotations, and
criticism of discrimination must appear in benign test examples.

Do not infer fairness from a moderation result. Structured Outputs constrains
response structure, not truth, safety, or fairness. Neither validation nor an AI
checker replaces authorization at tool and data boundaries.

## Dynamic policies

Select checks using trusted application configuration and approved request
context. Never allow user text to change policy or silently relax required checks.
Future policy changes are drafts until an administrator publishes a version.
The prototype selects policy by application and version, and schema by stage.
Versioned JSON policy files load at startup through VALINATOR_POLICY_FILE.
Request-type routing and live policy management remain later milestones.
All adapters receive one canonical content field; JSON schema checks parse that
same content when content_format is json. A separate payload field is rejected.

## Acceptance and delivery

Version 0.4 adds pre-parser envelope size and nesting controls, supported format
assertions, declarative cross-field comparisons, stage-specific checker actions,
and a ten-group coverage catalogue shared by input/output APIs and the dashboard.
Unsupported, disabled, partial, and simulated coverage must remain distinct from
passed checks. The example business policy compares submitted dates and amounts;
independent verification of business facts requires trusted context integration.

The foundation changes were informed by OWASP's Input Validation Cheat Sheet,
Nightfall's model input validation overview, Splunk's validation lifecycle article,
and review of the tools in Galileo's comparison. Use vendor primary documentation
for integrations: the comparison's Bedrock-only limitation is incorrect and its
linked Galileo Protect solution is deprecated in favor of Agent Control.

Additional references:
- https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html
- https://www.nightfall.ai/ai-security-101/model-input-validation
- https://www.splunk.com/en_us/blog/learn/ai-model-validation-ml.html
- https://galileo.ai/blog/best-llm-input-output-validation-tools
- https://python-jsonschema.readthedocs.io/en/stable/validate/

Test decision semantics, application isolation, timeouts, error privacy, and
schema boundaries before a live adapter. Before a pilot, add reviewed cases with
real violations and benign lookalikes; report precision, recall, false alarms,
sample counts, latency, and cost by checker. Pilot thresholds and hosting are
open decisions. No universal bias-free or prompt-injection-proof claims.

## References

- https://developers.openai.com/api/docs/guides/moderation
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://aws.amazon.com/bedrock/guardrails/
- https://docs.nvidia.com/nemo/guardrails/
- https://microsoft.github.io/presidio/
- https://github.com/promptfoo/promptfoo
- https://doi.org/10.6028/NIST.AI.600-1

## Labeled text completeness (implemented)

`completeness` belongs to the Business rules group alongside `cross_field`.
Policies configure up to 50 `required_sections` with unique rule identifiers and
case-insensitive unique ASCII labels, stage scope, and a minimum non-whitespace
character count. Exact line-start `Label:` syntax is recognized, including
multiline bodies; any other valid label ends the preceding body. Missing, short,
or repeated required sections produce findings. Unsupported content format or
no stage requirements produces skipped, and required skipped checks produce an
incomplete evaluation. Existing stage actions, observe/enforce behavior, failure
handling, and redaction rechecks apply unchanged.

This is structural presence, not semantic/document adequacy. It does not interpret
Markdown, files, claims, consent, or medical/legal sufficiency. Labels within
quoted text are still literal labels. API findings contain rule identifiers and
generic explanations only. The local dashboard includes failing and editable
passing examples through the production check endpoints.

## Bounded text obfuscation signals (implemented)

The opt-in `text_security` adapter is the first limited checker in the Prompt
injection and manipulation group. It identifies selected hidden/control code
points in plain text and parsed JSON keys/string values. It excludes ZWJ/ZWNJ to
preserve common script/emoji usage. It also parses explicit case-insensitive
`base64:`/`hex:` whitespace-delimited tokens; standard Base64 or byte-pair hex
must decode as UTF-8. It flags encoded content even when benign and separately
notes selected controls inside decoded text. It never interprets, returns,
sanitizes, or recursively decodes that text, nor sends it to other adapters.

Per-request budgets: 16 tokens, 4,096 characters per token, 8,192 total encoded
token characters. Limits apply across all JSON units. Budget exhaustion or invalid
canonical JSON returns error, not passed. The required-check failure contract and
stage-specific policy actions apply normally. Findings are generic categories
without submitted or decoded values; `request_context` is outside coverage.

No semantic injection/jailbreak, encoded PII/secrets, arbitrary encodings,
homoglyph, Unicode-tag, image, or multi-turn detection is claimed. Signals can be
legitimate. The group limitations and dashboard examples expose this explicitly.
The implementation uses only existing Python standard-library functionality.

## API admission budgets (implemented per server process)

A bounded startup `ResourceLimits` contract configures sustained checks/minute,
application burst, application active checks, process active checks, and active
POST `/v1/` requests. Defaults are 120/minute, burst 20, and concurrency 4/16/32.
Environment configuration uses `VALINATOR_` plus each upper-case field name;
invalid settings fail startup. Constructor injection overrides environment limits.
No remote service or new dependency is required.

A thread-safe token bucket uses a monotonic clock and trusted credential-bound
application identifiers; multiple keys and stages share application state. After
request/auth/application/policy validation, check admission tests application and
process concurrency before consuming a token and acquiring a slot. Rejections
are immediate: application limits 429, process limits 503, sanitized detail,
`Retry-After` and `Cache-Control: no-store`. Tokens are not refunded after an
admitted failure or cancellation. Slots are always released in `finally`.

An outer ASGI gate rejects excess POST ingress before JSON buffering/validation,
including unauthenticated traffic. GET endpoints stay available. Authenticated
application metadata exposes configuration and `scope: server_process`, not
usage or other applications. The dashboard explains two checks per run and holds
previews with retry guidance on 429/503. Browser tests use explicitly higher
allowances; actual admission is covered by real concurrent ASGI tests and a
thread-contention test without timing sleeps.

Scope: process-local state resets on restart; uniform settings apply separately
to each application. No shared worker/replica quota, per-plan overrides, hard
background-thread/CPU limit, connection protection, or model-cost cap is claimed.

## Local dashboard launcher

`start_dashboard.py` supplies an optional cross-platform first-run path. Python
3.11–3.14 is required for the complete PII-enabled playground. It creates an
ignored `.dashboard-venv` with pip, installs the existing `.[pii]` dependency set,
and records a successful setup fingerprint after `pip check`. Interrupted setup
is retried. Project/Python changes invalidate the marker; `--reinstall` explicitly
repairs dependencies. Installation may access package registries on first use.

The child server uses an absolute playground policy path and a fresh random
in-memory API key. Old API-key/PII/policy environment values are overridden;
resource-limit settings remain effective. No key file, URL credential, shell
activation, or paid model service is involved. The launcher binds a loopback
socket before printing the key or opening the browser, so an occupied port cannot
open an unrelated server as if it were the new instance. The browser opens only
after local readiness, without routing readiness traffic through a proxy.

The console explains connection and shutdown; `--port`, `--no-browser`, and
`--reinstall` are documented. The full manual API setup remains available.
Subprocess smoke tests verify all policies, fresh-key authorization, startup from
a different working directory, and occupied-port failure. CI includes the Windows
launcher path alongside Linux Python and browser checks. There is no hosted URL
or production deployment in this change.

## Optional external prompt-attack adapter

`azure_prompt_shields` uses Azure Content Safety's stable `2024-09-01` API,
input/plain-text only, with a conservative 4,000-character local cap. The adapter
is registered only with explicit enablement, endpoint, and subscription key.
Public-Azure custom resource origins over HTTPS/443 are accepted; origin
credentials, paths, queries, redirects, proxies and arbitrary hosts are rejected
or disabled. Outbound JSON contains only `userPrompt` and empty `documents`.
No request context, platform credential, proposed answer, or policy/tenant ID is
included. The default launcher explicitly disables this integration.

One bounded asynchronous request is attempted with no retries or fallback, under
the policy deadline. Responses are bounded to 16 KiB, parsed as strict JSON, and
must contain an actual Boolean `userPromptAnalysis.attackDetected`. HTTP,
transport, parsing, deadline and local-cap failures are sanitized error results;
required failed checks are incomplete and apply the policy failure action.
Provider bodies and submitted values do not appear in results. The sample policy
is observe-only and keeps local text-security checks on input/output; output
semantic prompt-attack analysis is not claimed.

Checker metadata now includes `external`; registered adapters declare the same
attribute so timeouts/errors preserve routing information. Any selected external
result, including redaction rechecks, makes cost nullable/unpriced:
`cost_usd: null`, `cost_basis: provider_unpriced`. Local-only behavior remains
zero/local_only. This is conservative capability metadata, not a billing receipt.
The dashboard discloses Azure routing before execution, flags external results,
and never describes provider cost as zero. The API version does not pin Azure's
model weights/version.

All provider tests use local HTTP fixtures. No live activation, calibration,
region/retention guarantee, paid call, or resource provisioning has occurred.
The local-first policy gate described below now holds provider calls until all
configured local checks pass. Live calibration remains an explicit backlog item. See
`docs/provider-decisions.md` and the README activation/data-handling contract.


## Local-first external gate

Policy `external_checks` defaults to `disabled`. Selecting an external adapter
alone does not permit data transfer. Explicit `after_local_pass` permission runs
all selected local adapters first, then permits registered external adapters only
when at least one local adapter ran and every local result is real and passed.
The policy/action/required flags cannot turn a local finding, optional error,
skip, missing adapter, or simulated pass into outbound permission. Observe mode
also holds egress. Gate failures return skipped external results with
`external_policy_disabled` or `external_local_gate`, retaining external/version
metadata. Required skips remain incomplete under existing failure semantics.

Execution preserves configured result order even if external rules are listed
first. Local checks can run concurrently with each other, then external checks
can run concurrently with each other. `timeout_ms` remains per checker; total
latency can include both phases. Request admission budgets cover the full call.
Cancellation during the local phase never starts the external phase. Custom
external adapters must truthfully declare the `external` registration attribute.
This is an engine policy gate, not a network firewall for arbitrary custom code.

Redaction revalidation uses exactly the same phased executor. Original content
that triggered a local finding is withheld. With required external skips, the
original evaluation is incomplete and same-call redaction stays withheld; use a
separate local-only redaction call for that workflow. With optional external
rules, a transformed request can reach the provider only after all transformed
local checks pass; bad transformed content cannot bypass the gate.

The Azure sample now requires actual local PII, secret, and text-security
adapters. The optional `pii` extra and enable flag are necessary for that sample;
missing PII support holds egress. The dashboard explains permission/gating before
execution and gives a specific withheld reason afterward. Cost stays conservatively
provider_unpriced for selected external checks, even when withheld; it is not a
receipt of a billable request. Detector limits still apply: passing configured
checks does not establish complete privacy, lack of PHI, or safe model behavior.

## Local regression suites and reports

`python -m valinator.evaluate` loads a strict bounded suite of up to 100 uniquely
identified cases from at most 256 KiB of JSON. Each case contains group/stage,
canonical CheckRequest, and explicit expectations for evaluation status,
recommended/effective actions, exact checker-status mapping, exact finding-ID set,
redaction status, and simulation. Cases are independent and sequential; expected
results are hand-written, never generated from actual results. Unknown policy
references are validated before execution. Group filters select subsets.

Only local built-in adapters are registered by the CLI, independently of provider
environment settings; injectable evaluation engines with registered external
adapters are refused before execution. This is an engine regression report, not an
API/auth/rate/ingress test or a live-provider calibration interface. The bundled
30-case suite contains real local guards and two intentionally incomplete cases.
Missing PII support does not silently remove tests.

JSON and standalone HTML reports contain assertion outcome/differing field names,
actual metadata (decisions, statuses, finding IDs, redaction status, simulation),
and timing. No request/content/context/redacted content or provider bodies are
copied. Expectation matches, underlying incomplete evaluations, and simulation
are separately displayed; no accuracy/compliance/correctness claim is made.
HTML escapes labels, has no script/network dependencies, and applies a restrictive
CSP. Existing reports need --overwrite; source aliases and duplicate destinations
are refused. Exit status is 0/1/2 for matched/mismatched/configuration failure.

`start_dashboard.py --evaluate` reuses the isolated complete environment and
creates a unique ignored `.valinator-reports/` run directory, then optionally opens
the HTML file. It starts no API server and creates no platform/provider key.
The full manual CLI supports custom file paths/group filters. See README for
commands, limits, report privacy, and the distinction from detector evaluation.

## Regression baseline comparison

`valinator.compare` validates source-free format-1 local reports (256 KiB max,
100 unique cases each), typed statuses/decisions, finite nonnegative timings,
consistent assertion metadata and recomputed summaries. Reports with unknown
source fields or different suite IDs are rejected. Comparisons align case IDs and
show added/removed cases, changed group/stage identities, new/resolved mismatches,
changed outcomes, and changed mismatch details. Finding/difference identifier
ordering is normalized. Latency deltas are separate observations, not outcome or
accuracy gates. Comparison unions contain at most 200 cases.

Inputs/expected definitions are absent from reports, so equality of those
artifacts is not verified. Suite-ID discipline and stable case IDs are required;
updated expectations cannot hide an actual outcome change in the comparison.
No complete provenance/accuracy claim is made. Standalone JSON/HTML comparison
outputs preserve input-alias and overwrite protection, escape rendered metadata,
and have no active script/network dependencies. Exit codes are 0/1/2 for no
current mismatches/current mismatch/invalid configuration; --fail-on-change also
flags non-timing changes, including additions/removals.

The evaluator optionally compares before writing its ordinary format-1 report,
embeds a comparison section in HTML, and can save separate comparison JSON.
Source baseline paths are protected from overwrite. The launcher automatically
selects the newest bounded candidate matching the suite ID, validates it fully in
the prepared interpreter, and preserves all prior run directories. --baseline
selects a reference explicitly; --no-compare opts out. The default local-only and
no-source-in-reports guarantees remain unchanged.

## Secrets group batch (0.15.0)

The bounded offline adapter now imports selected upstream expressions for sixteen
credential/identifier families plus a prefix-based fine-grained GitHub candidate
rule. It does not call plugin verification, file/baseline scans or user-controlled
suppression filters. Existing checker/finding IDs remain stable; version metadata
uses valinator-patterns-2. Private-key/JWT/identifier results do not establish
credential activity or cryptographic validity.

Policy options default off: detect_secret_assignments inspects bounded quoted
assignments and JSON string-field context for supported names, exempting selected
reference/placeholder syntax; inspect_encoded_secrets scans exactly one explicit
Base64/hex UTF-8 layer under shared 16 payload / 4096 token / 8192 total budgets.
Invalid/over-budget requested inspection is incomplete. Supported literal
assignments over 512 characters are incomplete. All work shares a local .2-second
scan budget; timeouts never produce partial success. Canonical text is unchanged.

confidential_terms has up to 50 typed rule IDs, 3–200-character literal terms and
stage scopes. NFKC/casefold matching uses a scan copy. Duplicate IDs/normalized
overlapping terms, invalid stages, and secret options without a secrets rule fail
startup. Findings expose confidential.RULE_ID with no matched term/excerpt/hash.
This is configurable literal matching, not document/IP/intent classification.

An extended observe profile, enforce batch profile, 100-case group suite, three
dashboard examples, whole-group launcher option and maintained user guide are
included. Reports remain source-free; eight group cases intentionally expect
incomplete results. Base options stay off for compatibility. The Azure sample
explicitly enables assignment/encoding local screening before egress. See
user-guide.md and secrets-coverage.md for examples, cases, failure behavior,
privacy boundaries and remaining gaps. No new dependencies or external calls.

Credential-positive examples may use an optional named synthetic_fixture with an
explicit [SYNTHETIC FIXTURE] plain-text request. A fixed local registry constructs
only inert example values, which are validated as canonical requests before
execution. Unknown names and ambiguous/non-placeholder requests fail suite load.
No arbitrary imports, eval, file/network lookups or real credential creation is
supported. This mechanism is exclusive to offline regression input, not API
requests. Report output continues to omit constructed values.


## Supported privacy-field batch (0.17.0)

`detect_labeled_sensitive_data` is a strict optional policy Boolean, default false,
requiring a pii rule. It enables bounded explicit-field heuristics for phone,
birth date, medical-record, health-plan and bank-account candidates. It shares
PII deadlines, transformations, sanitized findings and required-error handling.
Field attempts share a 64-field document budget; values over 128 characters
produce `pii_field_limit`, never partial success. JSON keys label scalar values
and scalar lists; plain text uses complete colon/equals labeled lines.

Combined conversation and redaction playground profiles enable it; legacy pii-v1
retains its original scope. Privacy evaluation has 100 cases (12 intentional
incomplete cases), offline, without accuracy/PHI/compliance certification.
Eight supported types can be replaced, then all configured checks rerun; sensitive
keys or schema-invalid replacements withhold content. Exact supported labels,
examples and limitations are maintained in the user guide and privacy coverage.


## Independent dashboard examples (0.17.1)

Input and output editors have separate example catalogs: 19 input and 21 output
examples, including output-only confidential disclosure and multiple-issue replies.
Loading an example changes only that stage's content/format, never the other
stage or shared profile. Input selection invalidates both results; output
selection preserves the completed input result and invalidates output only.
Examples make no network calls. Focused samples require explicitly selecting
the corresponding profile; valid clean examples adapt to JSON/completeness
profiles so users can prepare a passing input without overwriting the answer.


## Structure group batch (0.18.0)

structure-v1 defines separate request/reply JSON envelopes with local definitions,
types, required/extra fields, choices, patterns, lengths, numeric/array constraints
and seven supported formats. Schema checker version 2 preserves existing finding
IDs and reports a fixed safe first-problem reason, without values, names or paths.
Time now requires an RFC3339 timezone; time/date-time reject whitespace. Leap
seconds remain unsupported. This is a documented format compatibility correction.

The launcher supports --suite structure (100 cases, four intentional incomplete
and two optional skips). Core CI runs it without PII. Seven independent examples
are added per stage, bringing totals to 26 input / 28 output examples. Default
conversation rules are unchanged. No paid call, new dependency, exact-decimal
precision guarantee or hard CPU isolation is introduced. See the user guide.


## Business group batch (0.19.0)

Cross-field checker version 2 retains strict bounded JSON validation, then decodes
that canonical text with Decimal float tokens. Raw-number comparison no longer
rounds through a binary float; no arithmetic/rounding/conversion engine is added.
All six operators and ISO calendar dates remain supported. Missing/invalid
operands have a distinct safe explanation from a failed relationship; values and
paths are not returned. Public rule IDs remain in finding IDs/explanations.
Completeness version 2 distinguishes missing, repeated and short sections.

Business evaluation has 100 cases, six intentional incomplete skips and no
simulators. The launcher supports --suite business; core CI runs it without PII.
Focused numeric/date/refund/section samples keep editors independent and preserve
raw decimal strings through the browser. Only examples applicable to the active
profile are visible. Totals are 36 input / 39 output across all profiles. The
normal conversation profile is unchanged. No new dependency or provider call.
Both operands remain submitted data, not trusted server-side limits; no general
business truth, semantic adequacy or financial/clinical/legal approval is implied.

Comparison float tokens additionally reject encoded exponents with absolute value
above 1,000,000 before Decimal construction, making extreme-input decisions
consistent across supported Python runtimes. Existing strict JSON limits remain.


## Supported local text-security batch (0.20.0)

Text-security checker version 2 preserves four finding IDs and selected-control,
explicit-encoding, single-layer scope. It adds fixed control classes and invalid
encoding reasons, without payloads, positions, code points or decoded text. No
semantic attack detector, Unicode normalization or new detection model is added.
The existing shared budgets are 16 labels, 4,096 characters/token and 8,192 in
total across JSON keys/values. Exceeded budgets and invalid JSON remain errors,
not partial successful assessments. Request context/files are not inspected.

The batch has 100 cases, 14 intentional incomplete outcomes and explicit semantic
and legitimate-Unicode gaps. --suite text-security selects it; core CI runs it
without PII. Ten independent examples per stage bring catalogs to 46 input / 49
output. The focused profile visibly states attack intent is not assessed.
Normal conversation policies and optional Azure activation remain unchanged.
No provider call, dependency addition, live calibration or complete injection
protection is implied; supported scope and examples are in the user guide.


## Version 0.21: provider-independent content-safety checks

The core remains portable ASGI. CloudFront + Lambda is Greg's intended demo
hosting path, implemented separately by Greg. No deployment resources, Lambda
handler or hosting package are part of this validation-check batch. Hosting
must never select a validation provider. content_safety is a stable checker ID;
provider defaults to none, with an explicitly selected optional Bedrock adapter
and a tested replacement-adapter contract. AWS dependencies are optional extras
for this validation adapter, not requirements on hosting or the portable core.

The initial adapter requires a dedicated published version and full assessments
for five active text filters; detected=true is a finding even in provider detect
mode. Missing/inactive/malformed assessment, unsupported additional policy,
provider/credential/worker failure or timeout remains incomplete. No fallback,
model generation or deployment is automatic. Two admitted SDK workers retain
slots after engine cancellation until actual completion. These contracts have
offline fixtures; live calibration is pending. Existing safety/bias simulators
are not replaced with a false local keyword detector. See the user guide and
content-safety coverage inventory for commands and limitations.
