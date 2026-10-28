# Valinator

Policy-driven input and output checks for AI applications.

## Current milestone

This is a local API prototype. JSON Schema validation is real. Safety and bias
adapters are **simulators, not detectors**: they exercise policy decisions,
timeouts, errors, and review flags without calling any paid API. Every successful
simulator result is labelled `simulated: true`. An ordinary prompt receiving a
simulated `passed` result has **not** received a safety or bias assessment.

The prototype includes a local API-backed dashboard. It does not store prompt or
response content and has no database, live provider integration, or production
deployment yet.

An optional **real local detector** checks email addresses, credit-card candidates,
and selected U.S. SSN candidates using Presidio's patterns and validation routines. It is separate
from the simulated safety/bias adapters; see setup below.

## Open the dashboard with one command

Install Git and Python 3.12 (or 3.11–3.14), then download this repository:

```bash
git clone https://github.com/CollectiverseStudio/valinator.git
cd valinator
```

On **Windows PowerShell**:

```powershell
py -3.12 start_dashboard.py
```

On **Mac/Linux**:

```bash
python3.12 start_dashboard.py
```

The first launch installs the dashboard dependencies in its own ignored
`.dashboard-venv` directory and needs internet access; allow several minutes.
Later launches reuse that environment unless `pyproject.toml` or the launcher
Python changes. No shell activation or manually configured environment is needed.
The launcher selects all playground policies and the real local PII detector.
Python 3.15 still supports the core API, but not this complete PII-enabled profile.

The launcher prints a fresh **Application API key**, then opens
[the dashboard](http://127.0.0.1:8000/dashboard). Copy the key from the terminal,
paste it into **Application API key**, and click **Connect**. Keep the terminal
open. Stop with **Ctrl+C**. Restarting generates a different key; reconnect with
that new key. The key is never put in a URL or saved to a file.

If the browser does not open, visit the printed URL yourself. To use another port,
append `--port 8001`. Use `--no-browser` to skip automatic opening and `--reinstall`
to repair the environment. These options work on both systems. The server binds
only to `127.0.0.1`; this does not publish a website. Existing resource-limit
environment overrides remain effective. The launcher explicitly disables the Azure connector, including inherited enable settings.
No paid models or API calls are used.

After **Connect**, click a sample and **Run input and output checks**:

| Sample | Expected behavior |
| --- | --- |
| Clean text | Preview appears after local PII checks |
| Email address | Email finding; answer held |
| Redaction preview | Separate preview with supported identifiers masked |
| Missing information | Empty Next steps section flagged; fill it and rerun |
| Hidden characters | Input finding; answer held |
| Encoded text | Review signal; no decoded content returned |
| Credential pattern | Supported credential pattern flagged |

You supply both the message and proposed answer. The dashboard validates them;
it does not generate the answer. Passed checks cover only their stated scope.

## Run locally

Requires Python 3.11 or newer. From the repository directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
export VALINATOR_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvicorn valinator.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` and set the key:

```powershell
$env:VALINATOR_API_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Open http://127.0.0.1:8000/docs to use the API explorer. Click **Authorize** and
enter the generated key. `GET /healthz` is public; checking endpoints require it.
Without a configured key the checking endpoints return 503.

Open **http://127.0.0.1:8000/dashboard** for the visual playground. Enter your API
key, select a loaded policy, and supply the user message and proposed AI answer.
The dashboard calls the same authenticated APIs as an integrating application.
It holds the preview for findings or incomplete checks and labels simulated or
skipped coverage. It never generates an AI answer or claims that no findings
guarantees safety. Changing content clears earlier results.

Your key is held only in tab memory, not browser storage. Check content is not
saved as history. The dashboard is for local testing and is not a hosted service.

To try both local sensitive-data checks and structured business rules, install
`.[test,pii]`, enable `VALINATOR_ENABLE_PII=1`, and set
`VALINATOR_POLICY_FILE=policies/playground.json`. Select `pii-v1` for text or
`business-v1` for the JSON examples, then click **Business rule error** to see a
date-order and refund-limit violation. No model is called.

### Grouped coverage

Input and output results share ten groups: regulated/sensitive data, secrets,
prompt manipulation, harmful content, structure, scope, accuracy, bias, business
rules, and code/action safety. Groups show passed, findings, incomplete, simulated,
partial coverage, disabled, not applicable, or not implemented. Expand a group
for checker results and its limitations. A passed identifier check is not PHI
detection, and unimplemented groups never count as passed.

Input example (run in a second shell with the same key configured):

```bash
curl http://127.0.0.1:8000/v1/checks/input \
  -H "Authorization: Bearer $VALINATOR_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"application_id":"demo","policy_version":"support-v1","content":"{\"message\":\"Hello\"}","content_format":"json"}'
```

Use `content_format: "json"` to validate JSON encoded in `content`. Every checker
receives that same content; a separate `payload` field is rejected. For output,
the example policy expects the JSON object `{"answer": "Your response"}`.
Duplicate object keys, malformed JSON, and nonfinite numbers are flagged.

For plain text, use `content_format: "text"` (the default). The example policy
reports its schema check as skipped because it is optional. A policy requiring
schema validation reports an incomplete assessment if submitted content is text.
This is a breaking contract change from the initial prototype.

Try these exact content values to exercise the simulators:

| Content | Expected recommendation in the demo policy |
| --- | --- |
| `[SIMULATE:safety:FLAG]` | block |
| `[SIMULATE:bias:FLAG]` | flag |
| `[SIMULATE:safety:ERROR]` | incomplete assessment, block recommendation |
| `[SIMULATE:safety:TIMEOUT]` | incomplete assessment, block recommendation |

The demo policy is observe-only: `effective_action` is always `allow` while
`recommended_action` reports what enforcement would do. The caller must inspect
`evaluation_status`, checker results, and `simulated`, not only effective action.
Simulators are rejected in enforce mode unless explicitly enabled for testing.

## Tests

```bash
pytest -q
```

Browser interaction tests use the current stable Playwright release:

```bash
npm ci
npx playwright install chromium
npm run test:browser
```

Activate the Python environment first. Browser tests start their own local server
with the combined playground policy, so install `.[test,pii]` on Python 3.11–3.14. To use an
installed Chrome instead of downloading Chromium, set `PLAYWRIGHT_CHANNEL=chrome`.
The GitHub dashboard job uses the runner's installed Chrome.

Tests cover application scope, schema checks, observation versus enforcement,
failure handling, skipped required checks, response privacy, and simulator limits.

## Real local sensitive-data checks

Use Python 3.11–3.14; Presidio currently does not support 3.15. Install the optional
extra, enable the detector, and select the PII-only example policy before starting:

```bash
pip install -e '.[test,pii]'
export VALINATOR_ENABLE_PII=1
export VALINATOR_POLICY_FILE=policies/pii-only.json
```

In PowerShell:

```powershell
pip install -e '.[test,pii]'
$env:VALINATOR_ENABLE_PII = '1'
$env:VALINATOR_POLICY_FILE = 'policies/pii-only.json'
```

Submit `policy_version: "pii-v1"` with text such as `Contact jane@example.com`.
The response reports a real `pii.email_address` finding and `simulated: false`.
The example remains observe-only: it recommends flagging without blocking.
Input and output endpoints use the same detector. If the required detector is
disabled or unavailable, the result is incomplete, not a successful privacy check.

If PII is enabled without a custom policy, the demo policy also runs it alongside
the safety/bias simulators; the overall response remains `simulated: true`.

### Supported scope and limits

- Email patterns use a bundled domain suffix snapshot. Snapshot updates arrive
  with dependency updates; no domain-list download occurs during startup or scans.
- Credit-card candidates require a matching Presidio pattern and valid Luhn
  checksum. A match does not prove that an account exists or belongs to a person.
- SSN candidates use Presidio patterns and invalid-value exclusions, plus the
  SSA exclusion of area numbers 900–999. Standard hyphenated values may be
  flagged without context; unformatted, space/dot-delimited, and unusual formats
  require nearby SSN wording or a corresponding JSON key. Known example values
  and invalid combinations are excluded. These heuristics can miss identifiers
  or flag lookalikes; they do not verify issuance, identity, or ownership.
- JSON scanning decodes strings, keys, lists, and integer values. Escaped email
  characters cannot bypass checks by using a separate payload representation.
- Responses contain entity types and generic explanations, never detected values.
- Names, postal addresses, phone numbers, other government identifiers,
  obfuscated text, and arbitrary encodings are not covered.
- Supported patterns are driven directly with an internal scan deadline so a
  regex timeout becomes a failed check instead of silently skipping a pattern.
  Work runs in a thread; API timeout does not instantly terminate that thread.

No NLP models are downloaded, and no model or cloud endpoint is called. Presidio
still installs its normal package dependencies, including spaCy. Review the
supported subset and false alarms on application examples before enforcement.

### Optional redaction

Select `redaction-v1` from the combined playground policy or load
`policies/redaction.json` directly. Click **Redaction preview** for an example.
Only an administrator-controlled `pii` rule with `on_finding: "redact"` (or a
stage-specific override) enables transformations; request bodies cannot enable it.

Detected email, card, and SSN values are replaced with labelled placeholders.
The response's separate `redaction` object reports:

| Status | Meaning |
| --- | --- |
| not_requested | The policy did not request transformation; no content is returned |
| not_needed | No supported identifier was detected; no content is returned |
| preview | Observe-mode transformed content passed rechecks; preview only |
| ready | Enforce-mode transformed content passed rechecks; caller may apply the recommended action |
| withheld | Transformation or revalidation failed; no transformed content is returned |

All selected active checks are rerun against transformed content. Any non-pass
or simulated check withholds the transformation. Masking can change JSON types
or break formats; those results are also withheld. JSON keys containing detected
identifiers cause withholding rather than automatic renaming and possible key
collisions. Text outside matched spans is preserved; JSON values are reserialized.

Original findings remain visible. Observe mode still has `effective_action: allow`
and never silently forwards or replaces content. In enforcement mode, a validated
transformation recommends `redact`; an unavailable transformation recommends
`block`. The integrating application must use the returned transformed content,
not assume the check API itself modified a downstream model call.

Returned redacted content may still contain unsupported sensitive information.
This is limited pattern-based redaction, not full anonymization or a compliance
determination. Findings and checker results do not contain detected values;
only the explicitly enabled transformation field returns modified content.
Redaction applies only to `content`; the optional `request_context` field is not
scrubbed by this adapter. Integrators must assess every field they send onward.

## Configuration and boundaries

### Local secret patterns

Load `policies/secrets.json` directly, or select `secrets-v1` from the combined
playground policy and click **Credential pattern**. This adapter works without
the optional Presidio installation. Input findings recommend flagging; output
findings recommend blocking, while the example remains observe-only.

It reuses selected patterns from the current stable detect-secrets 1.5.0 package:

| Detector | Current scope |
| --- | --- |
| AWS | Selected access-key identifiers and quoted secret assignments with AWS context |
| GitHub | ghp, gho, ghu, ghs, and ghr token patterns |
| OpenAI | Supported sk-prefixed patterns containing the upstream marker; not all key formats |
| Stripe | Supported live secret/restricted-key patterns |
| JWT | Formally structured token candidates, without signature or claim verification |
| Private key | Selected PEM, OpenSSH, PGP, and PuTTY private-key markers, including incomplete blocks |

No provider verification endpoints are called. A pattern match does not establish
that a key is active, secret, owned by the caller, or cryptographically valid.
Public key blocks, ordinary references to passwords, and unrecognized formats
are outside the detection scope. Generic passwords, fine-grained GitHub token
formats not covered by upstream rules, proprietary documents, and obfuscated
payloads require separate work. There is no automatic secret redaction.

The scanner uses bounded regex execution and scans decoded JSON keys/values.
Caller-supplied allow comments and scanner suppression directives cannot disable
checks. Findings contain category names only, with no candidate values or hashes.
Timeouts and malformed JSON produce incomplete checks. The latest library version
does not imply exhaustive or continuously updated provider-format coverage.

The initial policy is an immutable startup snapshot in `demo_engine()`. To use
the included example policy file instead, set this before starting the server:

```bash
export VALINATOR_POLICY_FILE=policies/support.json
```

The JSON file requires `format_version: "1"` and one or more policies. Files are
limited to 1 MiB, schemas are validated at startup, and references outside the
local schema are rejected. Duplicate application/version pairs fail startup.
Policy files are administrator-controlled and are not accepted from API callers.
The environment API key still binds to `demo`; custom application credentials
are supplied through `create_app()` until a credential store is implemented.
Requests cannot change enforcement mode, supply a policy, or bypass required
checks. `create_app()` accepts an injected engine and credential-to-application
map for tests and future integrations. There is no policy management endpoint yet.

Do not expose this prototype publicly. Production work still includes shared multi-worker rate limits,
production identity and credential
rotation, tenant-aware persistence, audit metadata, deployment hardening, provider
data-routing controls, calibrated detectors, dependency security review, and
performance measurements. Request envelopes are limited to 256 KiB before
parsing, including streamed bodies, with a five-second read deadline. Only UTF-8
JSON and uncompressed bodies are supported. JSON is limited to 32 nested
containers, 1,000 entries per container, and 128 characters per numeric token.
These limits also apply to canonical JSON content inside a check request.

### Formats and cross-field rules

Schema `format` is actively enforced for date, date-time, time, email, IPv4, IPv6,
and UUID. Unknown formats fail policy startup instead of silently passing. Email
format checking does not perform DNS or mailbox verification. Formats do not
establish that a destination is safe or that an address belongs to a user.

The `cross_field` checker supports declarative `lt`, `le`, `eq`, `ge`, `gt`, and
`ne` comparisons of numeric values or ISO calendar dates, using absolute JSON
Pointers for `left` and `right`. There is no expression evaluation. Missing,
incorrectly typed, or invalid comparison values produce findings. The example
compares submitted fields; it cannot prove the original purchase amount is true.
Authoritative business data needs a separate trusted integration.

Checker rules can select `stages: ["input"]` or `["output"]` and override
`on_input_finding` and `on_output_finding`. The example business policy flags an
input comparison failure but recommends blocking an output comparison failure.
Observe mode still reports an effective action of allow.

No raw content is deliberately logged or persisted; your hosting/access logging
configuration must preserve this property. Policies are trusted server-side
configuration; untrusted remote JSON Schema references are not supported.

See [the specification](docs/specification.md), [implementation backlog](docs/backlog.md),
and [verified dependency versions](docs/dependencies.md).

### Required information in labeled text

The local `completeness` checker uses `required_sections` in a trusted policy.
See `policies/completeness.json`, or select **Missing information** in the
playground. The example requires an input `Summary:` and output `Summary:` plus
`Next steps:`. Input findings recommend flagging; output findings recommend
blocking. Observe mode still allows the request.

Each rule specifies a unique `rule_id`, an ASCII `label`, optional `stages`, and
`min_characters` (default 1; maximum 8,000). Up to 50 rules are permitted. Labels
must start at the beginning of a line and end with a colon; matching ignores
ASCII letter case. Text can follow the colon or continue on subsequent lines
until another label. Every syntactically valid label starts a new section, even
if it is not required. Repeated required labels are flagged as ambiguous.
Whitespace does not count toward the minimum length. Markdown headings, indented
labels, uploaded documents, and JSON fields are not interpreted as sections.
Use JSON Schema for structured required fields. A required completeness check on
JSON, or on a stage with no applicable requirements, reports skipped/incomplete.

Findings expose rule identifiers without submitted values. This is a literal
presence/length check: placeholders, false statements, malicious text, or quoted
labels can satisfy it. It does not assess legal clauses, symptoms, accuracy,
intent, bias, or semantic completeness. Combine it with other checks and trusted
review where those judgments matter. No external service or new dependency is
used.

### Hidden characters and labeled encodings

The opt-in local `text_security` checker flags selected invisible, bidirectional,
and control characters, plus explicit `base64: TOKEN` or `hex: TOKEN` markers.
See `policies/text-security.json`, or try **Hidden characters** and **Encoded
text** in the dashboard. Both input and output recommend flagging in the example
policy. These are review signals, including for benign encoded text, not proof of
an attack. Regular prose, unlabeled Base64-looking strings, normal newlines/tabs,
and zero-width joiners/non-joiners are not flagged by themselves.

Encoding markers are case-insensitive and tokens end at whitespace. Base64 must
use the standard alphabet and valid padding; hex must contain complete byte
pairs. Exactly one layer is decoded as UTF-8 and inspected for the same selected
controls. Malformed, empty, URL-safe-only, or non-UTF-8 payloads are flagged for
review. Decoded text is never returned or substituted for the canonical content.
Other checkers still inspect the original content: this feature does not detect
encoded secrets/PII, semantic instructions, arbitrary encodings, homoglyphs,
Unicode tags, multi-turn attacks, or all invisible Unicode characters. Legitimate
bidirectional controls and soft hyphens can produce review findings.

The scan accepts at most 16 labeled tokens, 4,096 characters in one token, and
8,192 token characters across the request. Exceeding any budget returns an error
and a required check becomes incomplete, applying the policy's failure action.
For JSON, bounded parsing happens first; decoded string values and keys share the
same budgets. `request_context` is not scanned. Findings contain categories only,
with no raw or decoded content. No network, model, dependency, or sanitization is
introduced.

Design reference: [OWASP LLM Prompt Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html).
A passed result means only that these narrow signals were not found; it does not
establish that a prompt is safe. A semantic injection detector remains planned.

### Request rate and concurrent work

The API now has startup-configurable admission limits. The defaults below apply
independently in **each server process**, with the same configuration for every
application:

| Environment variable | Default | Meaning |
| --- | ---: | --- |
| `VALINATOR_CHECKS_PER_MINUTE` | 120 | Sustained token refill rate per application |
| `VALINATOR_BURST` | 20 | Initial and maximum tokens per application |
| `VALINATOR_APPLICATION_CONCURRENCY` | 4 | Active checks per application |
| `VALINATOR_PROCESS_CONCURRENCY` | 16 | Active checks across applications |
| `VALINATOR_INGRESS_CONCURRENCY` | 32 | Active POST requests under `/v1/`, before body reading |

Use positive integer environment values and restart to change them. Rate is
bounded at 100,000, burst at 10,000, and each concurrency setting at 1,024.
Invalid values fail startup; zero does not disable protection. Python integrations
can pass `limits=ResourceLimits(...)` to `create_app()` instead. The dashboard
shows application limits from authenticated metadata.

All keys bound to an application and both input/output endpoints share its bucket
and check-concurrency count. Each admitted check spends one token, including a
check that later fails or is cancelled; a playground run submits two checks.
Tokens refill continuously at the sustained rate up to the burst capacity. This
is not a fixed-window ceiling: a fresh bucket permits a burst in addition to
ongoing refill. Invalid envelopes, failed authentication, application mismatch,
unknown policies, and concurrency rejections do not spend check tokens. A fresh
process/restart gets a fresh bucket; only trusted credential bindings create state.

Application rate/concurrency rejection returns **429**. Process check/ingress
capacity rejection returns **503**. Both include `Retry-After` in whole seconds
and `Cache-Control: no-store`, without submitted values or credentials. Rate
retry time is calculated from refill; concurrency retries use a one-second hint
and are not a reservation. There is no waiting queue, automatic retry, model call,
or successful validation decision for a rejected request. Slots release on
completion, exceptions, and cancellation. Health, metadata, dashboard, and static
GET requests are outside these limits; metadata remains authenticated.

The outer ingress gate includes unauthenticated POSTs, but is not a substitute
for upstream connection/DDoS controls. Check slots cover active API evaluations,
not hard CPU/thread budgets: background threads already running after a timeout
cannot be forcibly stopped. Multiple workers/replicas multiply local allowances;
shared quotas and worker/job isolation remain backlog work. These limits are not
billing or model-token spending caps.

### Optional Azure Prompt Shields connector

The `azure_prompt_shields` adapter implements Microsoft's stable REST API
`2024-09-01` for **user-input prompt-attack analysis**. It is off by default and
separate from the safety/bias simulators. It has been tested with local HTTP
fixtures, not a live Azure resource or a calibrated accuracy dataset. The model
version is managed by Azure; `checker_version` records the API version, not a
pinned model checkpoint. Provider findings can be wrong and need context review.

Activation requires an Azure Content Safety resource and deliberate server-side
configuration. Install `pip install -e '.[azure,pii]'` on Python 3.11–3.14, configure these variables in
your environment/secret-management system, and use the manual uvicorn start path:

| Variable | Value |
| --- | --- |
| `VALINATOR_ENABLE_AZURE_PROMPT_SHIELDS` | `1` |
| `VALINATOR_ENABLE_PII` | `1` (required by the sample privacy gate) |
| `VALINATOR_AZURE_CONTENT_SAFETY_ENDPOINT` | Your `https://RESOURCE.cognitiveservices.azure.com` resource origin |
| `VALINATOR_AZURE_CONTENT_SAFETY_KEY` | Your Azure subscription key, never a browser/platform key |
| `VALINATOR_POLICY_FILE` | `policies/azure-prompt-shields.json` |
| `VALINATOR_API_KEY` | A separate platform API key for connecting to Valinator |

Use your resource's exact endpoint. Only public-Azure custom resource origins on
HTTPS port 443 are supported; sovereign clouds and arbitrary/private origins are
outside this adapter's configuration surface. Endpoint/key settings alone do
nothing unless the enable flag is `1`, a selected rule uses the adapter, and the
policy sets `external_checks: "after_local_pass"`. The policy default is
`external_checks: "disabled"`, which withholds registered external checks. A bad
enable flag or incomplete enabled configuration fails startup. The sample policy
uses observe mode, a 2-second per-checker deadline, Azure on input only, and
required local text-security, secret, and PII checks on both sides. Its policy
explicitly permits gated external checks; a missing PII adapter prevents egress. The one-command launcher always forces Azure
off to preserve its local-only behavior; use manual server startup to enable it.

**Data handling:** the original `content` is sent to the configured Azure
resource as `userPrompt` with an empty `documents` array. Platform credentials,
application/policy identifiers, `request_context`, and proposed output are not
sent by this connector. Inputs must be plain text of at most 4,000 characters
(a conservative local cap); unsupported/long input fails without a provider call.
There is no truncation, splitting, document scanning, or output-attack assessment.
The dashboard discloses routing before a run and labels external checker results.

**Local-first gate:** configured local checks run first. An external check is
withheld unless at least one local check ran and every selected local check
passed without simulation. Findings (even with an allow action), optional
skips/errors, missing adapters, timeouts, and simulated passes all hold egress.
The gate applies in observe and enforce modes, and repeats on transformed-content
revalidation. A skipped required external check makes the evaluation incomplete;
the dashboard explains why it was withheld. Original input is not sent when the
local gate fails. The gate controls Valinator's registered external checks, not
model calls made independently by your application.

This is limited to configured detector coverage. A local pass does not guarantee
that all PII, PHI, trade secrets, or malicious content was detected. Enabled
external checks still send original content after those checks pass. With a
required external check, an original local finding prevents same-call redaction
from proceeding because the full evaluation is incomplete; use a separate local
redaction policy first if you need an approved transformed request. Optional
external checks can defer to transformed-content rechecks, which must pass the
same local gate before any external call. Rechecks may incur additional charges.
Custom adapters must declare `external=True` for the gate to recognize them.

Enable only for data permitted to leave your environment. Choose your Azure
region and review its current data handling/retention terms yourself; Valinator
does not infer or verify residency or promise provider retention behavior.
Shared quotas/region controls remain deployment work.

**Reliability and cost:** no redirects, inherited proxies, retries, fallback
provider, or synthetic-success substitution. Transport errors, timeouts, provider
429/authorization failures, malformed responses, and oversized responses become
sanitized error results. A required failed check is incomplete, applies the
policy's failure recommendation, and holds the dashboard preview. Observe mode
still reports an effective action of allow; integrating apps must apply their
chosen enforcement policy. Provider error bodies and source values are not
returned. The response is bounded to 16 KiB and must contain a strict Boolean
`userPromptAnalysis.attackDetected`.

External checks report `cost_usd: null` and `cost_basis: "provider_unpriced"`,
including failures and locally rejected external checks, because Valinator has no
billing receipt or pricing calculation. This is not evidence that a billable
request was made. Local-only checks retain zero / `local_only`. Each registered
external adapter must declare `external=True` so routing/cost metadata survives
engine timeouts. The change makes `cost_usd` nullable; API clients must support it.
No live Azure calls or resources were created during development.

Provider references:
- [Prompt Shields REST contract](https://learn.microsoft.com/en-us/rest/api/contentsafety/text-operations/shield-prompt?view=rest-contentsafety-2024-09-01)
- [Official quickstart](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/quickstart-jailbreak)
- [Service/model update and pricing FAQ](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/faq)

## Run the local regression examples

After downloading/updating the repository, use the launcher with `--evaluate`:

```powershell
# Windows with Python 3.12
py -3.12 start_dashboard.py --evaluate
```

```bash
# Mac/Linux with Python 3.12
python3.12 start_dashboard.py --evaluate
```

It prepares/reuses the same isolated environment, runs 30 hand-written examples,
and opens a standalone HTML report. No API key or running API server is needed.
Use `--no-browser` to print the report path instead. Each run creates a new folder
under ignored `.valinator-reports/`, containing `report.html` and `report.json`;
previous reports are preserved. The command exits after generating the report.

Examples cover supported PII and redaction, credential patterns and benign
lookalikes, hidden/encoded text, scan-budget exhaustion, JSON structure/date
formats, cross-field business rules, and required text sections. Expectations are
stored in `evals/local.json`, separately from trusted `policies/playground.json`.
Each case specifies a unique ID, group, stage, canonical request, and expected
recommendation/effective action, exact checker-status map, exact finding-ID set,
evaluation status, redaction status, and simulation flag. Review intentional
policy/detector behavior changes before editing expectations; nothing updates
them automatically.

**An expectation match is not a safety pass.** Two bundled cases deliberately
expect an incomplete evaluation. The report displays their actual incomplete
status even when the expectation matches. Findings and observe-mode allow actions
are also shown separately. These examples check regression behavior, not detector
accuracy, precision/recall, fairness, compliance, or production readiness. They do
not assert exact redacted strings or offsets. Latency is an observation on this
machine, not a benchmark or enforced performance target.

Reports contain case IDs, group/stage, checker statuses, finding IDs, decisions,
redaction status, simulation flags, and timing. They omit submitted content,
request context, redacted text, provider responses, and expected source values.
Fixture files contain their input examples; keep committed examples synthetic.
HTML is standalone, escapes labels, has no scripts or network dependencies, and
can be opened directly in a browser.

The runner uses only local built-in adapters, ignores Azure enable/credential
settings, and refuses registered external adapters. It cannot perform paid/live
provider calibration. It calls the engine directly: API authentication, ingress,
rate limits, and actual external-transfer behavior are covered by the separate
API test suite, not this report. Missing optional PII support produces mismatched
expectations rather than silently dropping privacy cases.

For custom cases or automation, use the prepared environment directly:

```bash
# Mac/Linux; use .dashboard-venv\Scripts\python.exe on Windows
.dashboard-venv/bin/python -m valinator.evaluate --pii --cases evals/local.json --policies policies/playground.json --report results/report.json --html results/report.html
```

Repeat `--group` to select groups, for example `--group secrets --group structure`.
Case files use strict bounded JSON (256 KiB, at most 100 cases, no duplicate IDs or
JSON keys). Unknown policy references fail before any case executes. Reports use
exclusive creation; `--overwrite` permits replacing reports, but input files,
symlinks/hardlinks to inputs, and duplicate report destinations are rejected.
Exit codes: **0** all expectations matched, **1** one or more mismatches, **2**
configuration/report failure. Errors and mismatch descriptions do not echo input
text. See `tests/test_evaluate.py` for the contract and `evals/local.json` for the
editable starter suite.
