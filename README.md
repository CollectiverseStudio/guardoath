# Valinator

Policy-driven input and output checks for AI applications.

## Current milestone

This is a local API prototype. JSON Schema validation is real. Safety and bias
adapters are **simulators, not detectors**: they exercise policy decisions,
timeouts, errors, and review flags without calling any paid API. Every successful
simulator result is labelled `simulated: true`. An ordinary prompt receiving a
simulated `passed` result has **not** received a safety or bias assessment.

The prototype does not store prompt or response content and has no database,
dashboard, live provider integration, or production deployment yet.

An optional **real local detector** checks email addresses, credit-card candidates,
and selected U.S. SSN candidates using Presidio's patterns and validation routines. It is separate
from the simulated safety/bias adapters; see setup below.

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

Do not expose this prototype publicly. Production work still includes rate limits,
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
