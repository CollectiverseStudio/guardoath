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

An optional **real local detector** now checks email addresses and credit-card
candidates using Presidio's patterns and validation routines. It is separate
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
with the PII-only policy, so install `.[test,pii]` on Python 3.11–3.14. To use an
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
- JSON scanning decodes strings, keys, lists, and integer values. Escaped email
  characters cannot bypass checks by using a separate payload representation.
- Responses contain entity types and generic explanations, never detected values.
- Names, postal addresses, phone numbers, government identifiers, obfuscated text,
  and arbitrary encodings are not covered. There is no automatic redaction yet.
- Supported patterns are driven directly with an internal scan deadline so a
  regex timeout becomes a failed check instead of silently skipping a pattern.
  Work runs in a thread; API timeout does not instantly terminate that thread.

No NLP models are downloaded, and no model or cloud endpoint is called. Presidio
still installs its normal package dependencies, including spaCy. Review the
supported subset and false alarms on application examples before enforcement.

## Configuration and boundaries

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

Do not expose this prototype publicly. Production work still includes bounded
HTTP body handling at ingress, rate limits, production identity and credential
rotation, tenant-aware persistence, audit metadata, deployment hardening, provider
data-routing controls, calibrated detectors, dependency security review, and
performance measurements. Request field limits apply after JSON parsing.

No raw content is deliberately logged or persisted; your hosting/access logging
configuration must preserve this property. Policies are trusted server-side
configuration; untrusted remote JSON Schema references are not supported.

See [the specification](docs/specification.md), [implementation backlog](docs/backlog.md),
and [verified dependency versions](docs/dependencies.md).
