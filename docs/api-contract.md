# Hosted validation API contract

This document describes the implemented customer API. The live machine-readable
contract is [OpenAPI](https://guardoath.com/openapi.json); the public integration
guide is [guardoath.com/guide](https://guardoath.com/guide). A source change becomes
live only after exact-source release checks and deployment.

The OpenAPI server default is the relative `/` on the **current origin**.
Swagger Try-it-out therefore stays local when `/docs` is served locally, and
uses production when the specification is served by `https://guardoath.com`.
The canonical production origin is `https://guardoath.com`; it is documented,
not selected as an off-origin default for local tests. The explicit production
examples below are intentional production requests, unlike a local Swagger test.

The interactive `/docs` reference starts with **Validation**, then optional
**Model invocation**, customer
policy/access and diagnostics. Owner operations are grouped last and retain
their separate security boundary. Protected owner routes require
`X-GuardOath-Admin-Session` with signed recent MFA and live owner membership;
customer keys/sessions are insufficient. Public administrator sign-in/config
and initial setup endpoints do not require an already-issued owner token, but
retain their own credential, challenge, exact-subject grant and same-origin
requirements. Production owner POSTs and native/setup POSTs require
`Origin: https://guardoath.com`; this is not a substitute for authentication.
The manually parsed enrollment request bodies are not yet expanded into OpenAPI
request models; this customer-contract update does not claim complete bootstrap
API documentation. The owner UI/operator guide remains the setup workflow.

## What a request does

`POST /v1/checks/input` checks supplied input. `POST /v1/checks/output` checks a
supplied answer. Both use the authenticated tenant's saved controls and the shared
Engine. Neither calls an AI model, selects a provider/agent, executes a tool or
records a durable interaction. Output is an independent request: it does not
require a successful input check or prove where the answer came from. The caller
controls input → its chosen AI → output ordering and content release.

The separate optional [guarded model API](guarded-model-invocations.md) discovers
approved connections at `GET /v1/connections` and performs buffered text generation
at `POST /v1/interactions`, with a stable request ID, input/output checks and
content-free durable receipts. It requires explicit provider/storage/quota
configuration; demos cannot make paid calls. Bedrock/Boto3 and direct OpenAI are
the first adapters. Agents, tools, streaming and other product/provider connectors
remain separate work, alongside the broader
[AI/agent system-of-record contract](ai-agent-activity-system-of-record.md).
Adding a model or agent field to `/v1/checks/*` is still refused with 422.

## Authentication and production transport

| Purpose | Header | Authority |
| --- | --- | --- |
| Server-to-server validation | `X-GuardOath-Key` | One authenticated tenant; a production key does not grant customer settings/key management |
| Signed-in customer dashboard | `X-GuardOath-Session` | Verified Cognito customer access token; required for customer management |
| Direct local/legacy development | `Authorization: Bearer …` | Supported by the direct app, **not the production CloudFront credential transport** |
| Production POST body integrity | `x-amz-content-sha256` | Lowercase SHA-256 of the exact body bytes; not an identity credential |

Use one applicable credential. Do not send both customer headers; the existing
session path takes precedence rather than falling back to a key after failed
session verification. An administrator session is separate and cannot be
substituted for a customer credential. Production CloudFront replaces
`Authorization` with its origin signature, so server integrations use
`X-GuardOath-Key`.

For production POSTs, serialize JSON **once** as UTF-8, hash those exact bytes and
send them unchanged. A missing/mismatched hash can yield an edge 403, which may be
HTML/plain text rather than an application JSON error. Hash an empty byte string
when an endpoint deliberately takes an empty body. Send `Content-Type:
application/json` for JSON bodies. This requirement belongs to the production
edge; a direct local ASGI server does not gain a new hash check from this OpenAPI
documentation.

Keep customer keys on the customer's server, outside committed source, browser
bundles and logs. The public dashboard can obtain a **shared demo** key with
`POST /v1/demo-users/demo1/key` and the JSON body `{}` (including its exact-body
hash in production). This is the same mechanism exposed by the published UI;
it does not create a customer account. Demo settings and allowance are shared.
Use synthetic content and do not reset or save demo settings merely to test a
request. Customer registration alone does not activate paid checks/API-key
creation; verified active subscription entitlement is required.

## Request fields

| Field | Required/default | Actual meaning |
| --- | --- | --- |
| `application_id` | Required, 1–100 characters | Must equal the tenant/application authorized by the credential; the field does not grant access |
| `content` | Required, 1–32,000 characters | Text, or a serialized JSON string for `content_format=json` |
| `content_format` | `text` by default; `text` or `json` | Format inspected by enabled checks |
| `policy_version` | Optional for tenant credentials | Omit or use `tenant`; other values return 404. This is not a saved revision or transaction binding |
| `language` | `en` | Only `en` is accepted, irrespective of individual detectors' multilingual wording coverage |
| `request_context` | Optional string, at most 8,000 characters | Accepted but **not inspected**; not trusted instructions, model/agent identity or history |
| `validation_codes` | Omit or `[]` for hosted tenants | Nonempty tenant selections return 422; legacy local configured engines have a separate pack facility |

All extra fields are refused by validation endpoints. No top-level `provider`, `model`, `agent`,
`messages`, `conversation_id`, `request_id`, temperature or tool-selection
parameter exists in a `/v1/checks/*` request. The separate invocation contract
defines its own target and idempotency parameters. Tenant controls cannot be overridden in a
check request. Local legacy credentials must name their configured policy.

Submitted JSON can contain a `messages` array *inside* `content`; selected
prompt-attack rules inspect bounded submitted turn boundaries. Caller-supplied
roles remain untrusted text. There is no authenticated conversation store, role
promotion, or semantic-history guarantee. Content to inspect must be in
`content`, not hidden in the unchecked context field.

### curl: one real input check

Set `GUARDOATH_API_KEY` to your server-side key and
`GUARDOATH_APPLICATION_ID` to its tenant ID. For an intentionally selected shared
demo key, use the corresponding demo ID. The example uses only synthetic text,
prints no credential and makes one validation request without automatic retry.

```bash
: "${GUARDOATH_API_KEY:?Set your server-side API key}"
: "${GUARDOATH_APPLICATION_ID:?Set the tenant ID bound to that key}"

guardoath_body=$(python3 - <<'PY'
import json, os
print(json.dumps({
    "application_id": os.environ["GUARDOATH_APPLICATION_ID"],
    "content": "What is the return policy?",
    "content_format": "text",
}, ensure_ascii=False, separators=(",", ":")))
PY
)
guardoath_body_hash=$(printf '%s' "$guardoath_body" | sha256sum | cut -d ' ' -f 1)

curl --fail-with-body --silent --show-error --retry 0 \
  --connect-timeout 5 --max-time 20 \
  'https://guardoath.com/v1/checks/input' \
  -H 'Content-Type: application/json' \
  -H "X-GuardOath-Key: $GUARDOATH_API_KEY" \
  -H "x-amz-content-sha256: $guardoath_body_hash" \
  --data-binary "$guardoath_body"
```

Changing the endpoint to `/v1/checks/output` checks the answer you supply; it
does not generate an answer. An HTTP 200 Block is still a successful HTTP
request, so `curl --fail-with-body` alone is not a decision gate.

### Python: exact bytes with the standard library

This uses the same two environment variables and sends one request. It does
not call a model or automatically release any text. Transport/HTTP failure is
raised rather than converted into an allowed decision. Redirects are refused
so a response cannot forward the custom API-key header to another destination.

```python
import hashlib
import json
import os
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

payload = {
    "application_id": os.environ["GUARDOATH_APPLICATION_ID"],
    "content": "What is the return policy?",
    "content_format": "text",
}
body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
request = urllib.request.Request(
    "https://guardoath.com/v1/checks/input",
    data=body,
    method="POST",
    headers={
        "Content-Type": "application/json",
        "X-GuardOath-Key": os.environ["GUARDOATH_API_KEY"],
        "x-amz-content-sha256": hashlib.sha256(body).hexdigest(),
    },
)
opener = urllib.request.build_opener(NoRedirect())
with opener.open(request, timeout=20) as response:
    raw = response.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("Check response exceeds the client limit")
    result = json.loads(raw)

print(json.dumps(result, indent=2))
```

## Results, decisions and usage

`CheckResponse` contains `check_id`, `application_id`, `policy_version`, `stage`,
`mode`, `evaluation_status`, `recommended_action`, `effective_action`,
`findings`, `checker_results`, `groups`, `redaction`, latency and local-check
cost metadata. The check ID identifies this result; there is no durable receipt
lookup or input/output linkage endpoint. The response does not bind a pair of
requests to one policy revision; saved tenant settings can change between them.

- **Block:** do not dispatch that input or release that answer.
- **Flag/Allow:** follow the returned effective action and the configured review
  workflow. Neither proves content is universally safe. Preserve the distinction
  between findings and the tenant's enforcement mode.
- **Redact:** use transformed content only when the returned redaction is ready
  and its required recheck succeeded. A preview is not an authorization to
  substitute/release content. Withheld redaction is not a clean result.
- **Incomplete:** show the failed/unavailable checks and effective failure
  decision. Do not present it as a passed inspection. Existing privacy and
  attachment hard Blocks remain in force, including observe mode.

Completed HTTP 200 checks, including Block results, consume one recorded check.
There is no idempotency key: repeating a request is another check. Failed HTTP
requests do not establish a completed result, and a lost response cannot prove
whether the server completed and counted it. Do not add blind POST retries.
`cost_usd=0` with `cost_basis=local_only` describes the checks' external-provider
cost, not the customer's subscription or total AWS/model bill.

## Attachments and agent comparison

`POST /v1/checks/input/attachments` and `/v1/checks/output/attachments` add
`attachments_b64` to the same request: 1–3 strict Base64 strings, at most 45,000
encoded characters each. The inspection budget is 32,000 decoded bytes per
file. The full JSON envelope remains limited to 262,144 bytes. Actual bytes
activate inspection; filenames/MIME claims do not.

`AttachmentCheckResponse` adds one receipt per file: index, kind, recognition
status, byte count, SHA-256, reason code and evaluation status. CSV/TSV structure
recognition is not a clean scan. Unsupported PDF/Office/ZIP/images, invalid bytes
and incomplete required inspection fail closed. Explicit saved attachment Off
remains authoritative and returns `not_inspected_policy_off`; it is not labeled
passed. File redaction is not implemented.

`POST /v1/agent-activity/compare` takes `proposed` and optional `observed`
objects. Each contains exactly `tool`, `target` and nonempty JSON `arguments`.
Supported diagnostic tools are `shell.run`, `http.request`, `file.write` and
`file.delete`. Nothing executes. `AgentActivityResponse` reports
`no_observation`, `matching_unverified` or `mismatch`, always
`verified_execution=false`, `authorization=not_granted` and
`mode=diagnostic_unverified`. Matching caller reports are not signed host proof;
no receipt is persisted.

## Error contract

Application errors use `{"detail": "..."}`. Request schema failures do not
return the framework's submitted-input error dump. Network/CloudFront errors
can be non-JSON; clients must handle transport errors without releasing content.
The separately scoped owner enrollment finish endpoint additionally returns a
fixed `code: "code_mismatch"` on a confirmed invalid authenticator code. Its
documented 400 response permits that shape without permitting arbitrary echoed
fields. Enrollment bodies have a stricter 24,000-byte cap.

| HTTP | Meaning |
| --- | --- |
| 400 / 408 / 413 / 415 | Invalid JSON/headers/parser limits, body-read timeout, body too large, or unsupported media type/encoding |
| 401 | Missing/invalid credential or customer session |
| 402 | Customer subscription does not authorize the paid operation |
| 403 | Wrong tenant/management authority, or edge body-integrity rejection |
| 404 | Policy/resource/capability unavailable |
| 409 | Saved revision/state conflict on a management operation |
| 422 | Invalid contract fields or unsupported tenant pack selection |
| 429 | Capacity/rate/monthly allowance limit; optional Retry-After is not idempotency |
| 503 | Required configuration, identity verification or service unavailable |

These descriptions expose no provider credential and do not introduce new
runtime authorization rules. Protected administrative APIs retain their
separate owner/MFA authorization contract.
