# Guarded model invocation API

The optional model API and the dashboard's **Invoke a selected model** panel
share the same authenticated endpoints. Discover actual readiness first.
Deploying this code does not enable provider spending: without explicit storage,
credentials, approved targets and quotas, the catalog is unavailable and a
generation request returns 503. Public demos cannot invoke paid models.

| Method and path | Purpose |
| --- | --- |
| `GET /v1/connections` | Approved tenant connections, model aliases, limits and readiness |
| `POST /v1/interactions` | One guarded buffered text generation with durable idempotency |
| `GET /v1/interactions/by-request/{request_id}` | Recover metadata after a lost response |
| `GET /v1/interactions/{interaction_id}` | Read a tenant-owned receipt |
| `GET /v1/interactions?limit=20` | At most twenty recent receipts; no full-history pagination claim |

The real tenant application response advertises `invocation_catalog` as the
literal `/v1/connections`. Older servers without that capability retain manual
validation. Available means configured for a test; it is not proof that upstream
credentials or model access have already succeeded.

## Request and workflow

Use your tenant's `X-GuardOath-Key`, or a verified customer dashboard session.
Invocation requires a verified active subscription and enforce mode. Select
connection/model aliases returned by the authenticated catalog, enter one text
message and a token limit, and save a fresh request ID before sending.

```json
{
  "application_id": "your-application",
  "request_id": "logical-request-001",
  "target": {
    "kind": "model",
    "connection_id": "approved-connection",
    "model_id": "approved-model-alias"
  },
  "message": "Explain the return policy in one paragraph.",
  "max_output_tokens": 256
}
```

These connection/model names are placeholders. An alias resolves to trusted
configuration containing the exact provider ID, credential binding, region,
service tier and connection version. Extra fields are refused before dispatch.

| Parameter | Bound and meaning |
| --- | --- |
| `application_id` | Required; must match the authenticated tenant |
| `request_id` | Required; 1–80 permitted identifier characters; unchanged on identical retries |
| `target.kind` | `model` only |
| `target.connection_id` | An approved connection belonging to this tenant |
| `target.model_id` | An approved alias in that connection |
| `message` | Required text, at most 16,384 UTF-8 bytes |
| `max_output_tokens` | Required strict integer, 1–4,096 and within the selected model's ceiling |

Agents, tools, attachments, images, streaming, conversation reuse, temperature,
provider URLs, credentials, role overrides and arbitrary provider parameters are
unsupported. Provider output-token accounting can include reasoning. The original
checked answer is limited to 32,000 UTF-8 bytes.

The dashboard saves pending policy changes before execution, masks credentials
in copied examples, and discards late responses after tenant/session changes.
**Refresh receipt** reads metadata without another generation. Reopening a
receipt does not retrieve the answer: the ledger retains neither prompt nor
answer. The original response is available only in the original POST result.

### Production Python example

Set application, connection and model from the actual catalog. Keep
`GUARDOATH_REQUEST_ID` unchanged if this operation needs recovery. This makes one
request, hashes the exact transmitted bytes and does not follow redirects.

```python
import hashlib
import http.client
import json
import os

payload = {
    "application_id": os.environ["GUARDOATH_APPLICATION_ID"],
    "request_id": os.environ["GUARDOATH_REQUEST_ID"],
    "target": {"kind": "model",
               "connection_id": os.environ["GUARDOATH_CONNECTION_ID"],
               "model_id": os.environ["GUARDOATH_MODEL_ID"]},
    "message": "Explain the return policy in one paragraph.",
    "max_output_tokens": 256,
}
body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
connection = http.client.HTTPSConnection("guardoath.com", timeout=15)
try:
    connection.request("POST", "/v1/interactions", body=body, headers={
        "Content-Type": "application/json",
        "X-GuardOath-Key": os.environ["GUARDOATH_API_KEY"],
        "x-amz-content-sha256": hashlib.sha256(body).hexdigest(),
    })
    result = connection.getresponse()
    data = result.read(1_000_001)
    if len(data) > 1_000_000 or result.status != 200:
        raise RuntimeError("Retain the request ID and inspect/recover its receipt")
    value = json.loads(data)
    approved_answer = (value["response"]
        if value["receipt"]["status"] == "completed" else None)
finally:
    connection.close()
```

Use the same credential for discovery/recovery GETs; a bodyless GET has no POST
body-hash requirement. Never insert credentials into a prompt or copied example.
HTTP 200 alone does not authorize using output: inspect `receipt.status` and
`response`. The OpenAPI reference contains the exact generated schemas.

## Enforcement, retries and accounting

One validated tenant snapshot and the existing shared Engine determine input
and output. Explicit Off remains authoritative. Incomplete, simulated or blocked
input cannot dispatch. Redaction forwards/releases only transformed text that
the Engine rechecked. A changed tenant revision or entitlement prevents release
under stale authority.

### Employee chat and human-safety routing

An application or chat interface can submit its message to this endpoint without
an AI agent. The selected model returns text; the same saved tenant policy checks
that text before the original POST can release it. This covers requests routed
through the endpoint, not unrelated conversations in external chat products.

The existing **Human safety** and **Harmful content (wording)** controls apply to
invocations exactly as they do to manual checks. Their Input and Output actions
and individual rule overrides remain tenant-owned. The following is an example
policy to configure deliberately, not a new default applied to customer records:

| Situation | Example saved action | Endpoint behavior |
| --- | --- | --- |
| A person asks for help and matches a crisis indicator | Human safety Input **Flag** | Records the finding and can invoke the approved model; the answer still must pass Output checks. |
| A known method-seeking or violent request matches a rule configured to block | That individual Input rule **Block** | Returns `input_blocked`, no answer and zero model dispatches. |
| The model returns known self-harm encouragement or encouragement to hurt another person | Human safety and Harmful content Output **Block** | Returns `output_blocked` and no model answer. Matching rule IDs remain in the receipt. |
| A saved check or individual rule is Off | **Off** on that stage/rule | Does not enforce that disabled control; other enabled controls still run. |

Configure these actions in the existing Policy section before using **Run a model
through GuardOath**. A Flag is a recorded finding that permits continuation. It
does not notify a person, arrange assistance, or guarantee a supportive model
answer. When output is withheld or generation fails, the customer's chat must
provide its own reviewed supportive message and appropriate local help options;
this API currently returns `response: null`, not an automatic crisis response.

`tests/test_invocation_human_safety.py` exercises actual hosted authentication,
tenant policy, both production wording checkers, the shared Engine and durable
SQLite receipts with an offline model fixture. It covers flagged help requests,
explicit Input blocks with no dispatch, harmful Output withholding, simultaneous
findings, benign support examples, saved Off, and content-free receipt recovery.
It does not establish live inference, semantic or multilingual accuracy, clinical
assessment, reliable intent detection, or protection for every paraphrase. These
checks match known wording; quoted or educational material can also match.

### Durable dispatch and receipt accounting

An atomic durable transaction records dispatch intent and reserves one call plus
the requested maximum output tokens against the connection's monthly quota.
Credential/version rotation does not reset that counter. Each completed input
and output validation separately uses the existing check allowance. If output
capacity becomes unavailable after generation, its answer is withheld.

A stable keyed fingerprint binds the original request, excluding the latest
policy revision. An identical retry returns its original receipt,
`replayed: true`, and `response: null`; it never regenerates. The same ID with
different request content returns 409. Keep the original ID after any timeout.

| Receipt status | Meaning |
| --- | --- |
| `completed` | Original POST can contain checked text; receipt-only replay does not |
| `input_blocked` / `input_incomplete` | No provider dispatch |
| `output_blocked` / `output_incomplete` | No answer released; provider execution may have occurred |
| `provider_withheld` | Unsupported, filtered, incomplete or unusable provider output |
| `provider_rejected` | Inspect `dispatch_state`; a local refusal is not an upstream response |
| `outcome_unknown` | Preserve the ID and reconcile before a fresh logical generation |
| `quota_exceeded` / `unavailable` | No usable answer; inspect `reason_code` |
| `admitted` / `input_checked` / `dispatch_intent` / `provider_result` | Nonterminal; never automatically resume an abandoned attempt |

Cancellation cannot prove a remote provider stopped. Worker slots remain occupied
until synchronous calls end. There are no automatic provider retries. Uncertain
outcomes retain reservations. Reservations are conservative and are not refunded
or settled to actual usage in this version. Call/token quotas are **not verified
dollar caps**; provider-reported usage is **not an invoice**.

The terminal receipt must persist before checked text is returned. Completion
means prepared for HTTP release, not proof of client receipt. Storage failure
withholds output. Receipt reads are tenant rate/concurrency limited and do not
increment the paid validation counter.

## Evidence and provider scope

Receipts contain bounded identifiers, approved target/version, policy revision
and digest, checker identifiers/versions/status, finding IDs, decisions, provider
request/response IDs, observed model when actually supplied, usage provenance and
a signed event chain. Missing observations stay null. Bedrock Converse does not
report an observed model; its requested model is recorded separately.

The ledger retains no prompt, answer, full policy, provider credential, raw error,
tool argument or reasoning content. Content retention/replay, encrypted content
storage, deletion workflows and an independently witnessed archive are not
implemented. The original HTTP response and the customer's system can contain
approved text. Provider retention depends on that provider/account; OpenAI
`store:false` is not a universal retention guarantee.

The first adapters are **AWS Bedrock Converse through Boto3** and **direct OpenAI
Responses**, with offline protocol and guarded API tests. Those tests do not
claim paid live inference. AWS binds an explicit approved role, region and
model/profile; the host's ambient identity is never silently used for inference.
Temporary role credentials are cached until near expiry. This initial credential
path constructs/closes a Bedrock client per call; pooled reuse is not claimed.
Boto3 buffers Converse before parsing, so accepted-structure bounds are not a
hard raw-wire allocation cap. The OpenAI HTTPS transport bounds raw reads.

Direct Anthropic, Azure/Microsoft, ChatGPT/Copilot/Claude product connectors and
hosted agents remain distinct additions. An Anthropic model hosted through
Bedrock is not a direct Anthropic-account integration. GuardOath does not
intercept unrelated activity or govern hidden remote agent tools. The broader
[AI/agent source-of-record contract](ai-agent-activity-system-of-record.md) is
not fully completed by this first model transaction.

## Explicit operator activation

Review the dedicated DynamoDB table capacity/storage, stable signing key,
provider role/key, exact model IDs, region/tier, call/token reservations and
provider pricing before activation. The separate storage template defaults off.
Do not reuse tenant or administrator audit tables. See the
[persistence and activation review](invocation-storage.md) for integrity-key
lifecycle, recovery, capacity, IAM and costs.

| Trusted setting | Required value |
| --- | --- |
| `GUARDOATH_INVOCATIONS_ENABLED` | Explicit `1`; default `0` |
| `GUARDOATH_INVOCATION_TABLE` | Dedicated `pk`/`sk` DynamoDB table |
| `GUARDOATH_INVOCATION_REGION` | Explicit approved commercial AWS ledger region |
| `GUARDOATH_INVOCATION_SIGNING_KEY` | Base64 of a persistent 32–64-byte secret |
| `GUARDOATH_AI_CONNECTIONS_JSON` | Bounded trusted connection registry |
| `GUARDOATH_PROVIDER_SECRET_*` | Explicit credential/external-ID secret references |

Registry entries contain `application_id`, `info` matching `ConnectionInfo`, and
`credentials`. OpenAI uses `{"kind":"openai_key","secret_env":
"GUARDOATH_PROVIDER_SECRET_OPENAI"}`. Bedrock uses `{"kind":"aws_role",
"role_arn":"<approved-role-arn>","external_id_env":
"GUARDOATH_PROVIDER_SECRET_AWS_EXTERNAL_ID"}`. The external-ID reference is
optional only when the reviewed role trust policy does not require it.
No credential-registration UI, customer endpoint selection or sovereign AWS
partition support is implied. Provider secrets never appear in the catalog.

Invalid activation disables invocation while preserving manual validation.
The existing 15-second Lambda setting is unchanged. The invocation deadline is
ten seconds, including admission, with a four-second provider allowance inside
it. These are fail-closed ceilings, not latency or successful-response guarantees.
