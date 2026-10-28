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

Input example (run in a second shell with the same key configured):

```bash
curl http://127.0.0.1:8000/v1/checks/input \
  -H "Authorization: Bearer $VALINATOR_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"application_id":"demo","policy_version":"support-v1","content":"Hello","payload":{"message":"Hello"}}'
```

For `/v1/checks/output`, use `payload: {"answer": "Your response"}`. `content`
is the text for contextual checks; `payload` is the parsed structured data for
schema validation. The caller is responsible for supplying corresponding values;
this prototype does not establish that they describe the same response.

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

Tests cover application scope, schema checks, observation versus enforcement,
failure handling, skipped required checks, response privacy, and simulator limits.

## Configuration and boundaries

The initial policy is an immutable startup snapshot in `demo_engine()`.
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

See [the specification](docs/specification.md) and [implementation backlog](docs/backlog.md).
