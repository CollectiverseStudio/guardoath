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

`pytest tests/test_tokatlas_adapter.py -q`

No live API call, model charge, AWS change or production deploy is part of these tests. The original prototype's `ProviderBinding` may be constructed with `TokatlasChatAdapter` by a **trusted host**, but no integration into `guardoath/app.py` is authorized until Director-owned API migration and deployment gates are met.

## Director integration acceptance

1. Tenant configuration chooses Tokatlas or a different provider independently of validation settings; model ID is allowlisted, token budget/rate ceiling enforced, and secrets never appear in prompts or responses.
2. Block input → zero provider call; redact input → provider receives only revalidated transformed text.
3. Provider reply is buffered, output-validated and blocked when tenant policy requires. Never enable unbuffered streaming by accident.
4. Provider timeout, malformed usage, error body, oversized response, missing key or disallowed model cannot leak raw output or switch to another provider without explicit tenant-authorized failover rules.
5. Tool proposals never run without a trusted dispatch authorization path.
6. Tests cover integration with an actual fake HTTP transport and an authenticated tenant, with exact-SHA 12-job CI before any production deploy.
