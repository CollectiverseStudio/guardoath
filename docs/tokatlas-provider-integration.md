# Tokatlas provider adapter — isolated development implementation

**Status:** Isolated adapter and offline unit tests only. Not registered in the hosted GuardOath API, not deployed, and no paid provider call made.

GuardOath's tenant-authoritative single-call gateway can invoke Tokatlas as **one provider option**, never as an alternative policy engine. The order remains: tenant-configured input validation → permitted provider call → buffered output validation → safe customer response. An input block must not send content to Tokatlas; a blocked output must not be returned.

## Verified public integration contract

Tokatlas documentation: https://www.tokatlas.com/en/docs-content/chat/overview and https://www.tokatlas.com/en/docs-content/using-the-api/overview.

- Endpoint: `POST https://api.tokatlas.com/v1/chat/completions`
- Headers: `Authorization: Bearer <server-side-key>` and `Content-Type: application/json`
- Text request: `model`, `messages=[{"role":"user","content":"..."}]`, `stream=false`
- Text reply: `choices[0].message.content` (Tokatlas also documents a `data` wrapper)
- Usage: `prompt_tokens` / `completion_tokens` when actually returned. The isolated adapter fails closed if missing; it does not invent token charges.

The adapter lives in `guardoath/gateway_prototype/tokatlas.py`, implements the prototype provider `async generate(message=...,model=...)` contract and returns a strict `ProviderReply`. Its address is fixed, credentials are supplied only by trusted host binding, the caller can select only an allowlisted model, streaming is disabled, tool proposals remain withheld by the gateway, response bytes are capped, malformed replies become sanitized provider errors and all offline tests inject a fake transport.

Do **not** pass a Tokatlas key in end-user request JSON or store a key directly in the browser, log, or GitHub. The Director should use a managed credential reference or trusted secret store, per-tenant entitlements, rate/cost controls, verified egress, charge reconciliation and provider-model allowlisting. Do not rely only on the HTTP adapter's timeout: `asyncio.to_thread` cannot stop an underlying blocked thread when cancelled. Production adapter execution requires outer concurrency budgets and network-level timeouts.

## Offline test command

`pytest tests/test_tokatlas_adapter.py tests/test_tokatlas_security.py tests/test_tokatlas_gateway_integration.py -q`

No live API call, model charge, AWS change or production deploy is part of these tests. The original prototype's `ProviderBinding` may be constructed with `TokatlasChatAdapter` by a **trusted host**, but no integration into `guardoath/app.py` is authorized until Director-owned API migration and deployment gates are met.

## Director integration acceptance

The default live transport is disabled until a supported upstream generation
spend accounting and cancellation controls are integrated by the Director.
Response-byte and call-count caps do not bound generation charges. Trusted model_limits explicitly allow models supporting max_completion_tokens,
n=1 and store=false; an absent model or invalid 1–4096 integer cap fails before
transport. No default model approval is inferred; client budget overrides fail.
Injected fake transports remain available for offline development.

The transport rejects every redirect (301/302/303/307/308), including same-origin
redirects, and accepts only the fixed HTTPS endpoint. Credentials and transport
objects are omitted from adapter representations, including nested provider
bindings. Offline canary tests cover errors and logs; integration tests prove
redacted input is revalidated before fake HTTP, direct/wrapped replies work,
invalid usage fails closed and tool proposals remain withheld. Only stop is a
complete text response; length, content_filter, missing/unknown reasons fail
closed. tool_calls always creates an opaque withholding marker. A present
application code must be integer 200. These contracts follow the documented
Tokatlas capability verification recorded in the coordination log.

Response JSON rejects duplicate object member names at every depth (including
escaped aliases), nonstandard NaN/Infinity constants, and multiple choices despite
the requested `n=1`. Ambiguous error/success codes or hidden tool proposals cannot
be resolved by silently selecting the last field or first completion. Errors stay
sanitized. Repeated names in separate objects and JSON-looking strings inside
completion text are valid; output validation still applies to that text.

1. Tenant configuration chooses Tokatlas or a different provider independently of validation settings; model ID is allowlisted, token budget/rate ceiling enforced, and secrets never appear in prompts or responses.
2. Block input → zero provider call; redact input → provider receives only revalidated transformed text.
3. Provider reply is buffered, output-validated and blocked when tenant policy requires. Never enable unbuffered streaming by accident.
4. Provider timeout, malformed usage, error body, oversized response, missing key or disallowed model cannot leak raw output or switch to another provider without explicit tenant-authorized failover rules.
5. Tool proposals never run without a trusted dispatch authorization path.
6. Tests cover integration with an actual fake HTTP transport and an authenticated tenant, with exact-SHA 12-job CI before any production deploy.

Gateway clients must inspect the body `status` and `X-GuardOath-Decision`:
HTTP 200 also represents a successful validation decision to withhold content.
