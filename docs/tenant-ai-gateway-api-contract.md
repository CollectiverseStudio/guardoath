# GuardOath — Tenant-Authoritative AI Gateway Contract

**Architecture decision:** Greg, 2026-11-01. **Status:** approved product direction / implementation contract, not a declaration that the end-to-end gateway is deployed.

## Product behavior

A customer application makes **one authenticated HTTPS API request** to GuardOath with an end-user message. GuardOath (a) resolves the authenticated tenant record, (b) checks user input according to the tenant's enabled validation controls and their stage-specific responses, (c) **only after input is permitted**, forwards the approved/transformed content to the configured AI provider, (d) checks the returned AI output against the same tenant's output-stage configuration, then (e) returns only the authorized safe output and decision metadata to the customer. Input block or incomplete-required-check fails *before* any AI call; output block withholds the generated text. This is a gateway product, not simply two separate caller-managed validation operations.

**Existing state (verified from source):** `guardoath/app.py` exposes `POST /v1/checks/input` and `POST /v1/checks/output`; the source does **not** implement this full provider-calling gateway sequence. `guardoath/tenant_record.py` stores 17 individual `check_<id>` action fields and builds `to_policy()`, with `null` disabling. `guardoath/hosted.py` currently loads externally defined example profiles which bypass tenant-field action choices when selected. Do not claim the new gateway is already integrated or publicly available until authenticated end-to-end verification.

## Architectural authority: one tenant, one policy

**Remove the hosted example-profile concept.** No runtime example policy selection, default profile choice or separate profile authority. An authenticated tenant record is the sole mutable source of optional validation selection and finding actions. Deprecate or reject `policy_version` profile selection on customer endpoints explicitly and document the migration; do not silently honor stale profile names. Demonstration examples may be input samples or explicit new-tenant onboarding defaults persisted into the tenant record, never alternative profiles at runtime. Preserve a stable internal policy revision derived from the tenant-record revision and control catalog version for audit; it is **not** a customer-selectable policy.

Do not conflate validation checkers, imported signatures, documented scenarios and test executions. Store one independent action configuration per **published executable validation ID**; families and sectors are labels, not implicit wildcard execution. A stable versioned control catalog (code-defined registry) enumerates available IDs, supported stages, content types, action capabilities, dependencies, cost class and maturity. It cannot override the tenant's configured selection. A tenant record holds a typed, size-bounded map `validations: {<stable-id>: {input_action?, output_action?, ...}}`, one individually addressable field per validation *inside the same record*, supporting expansion without schema changes for each new ID. Off is explicit `null`/disabled; `allow` still executes and reports findings; `flag` reports; `block` denies upon a finding; `redact` is only permitted for validators with tested transformations and must revalidate before forwarding/releasing. Enforce action/stage compatibility at write time. Apply updates using optimistic revision concurrency; reject unknown IDs, duplicate IDs, unsupported stages/actions, invalid/missing settings and unauthorized tenant changes.

**Immutable platform security boundaries** such as authentication, tenant isolation, provider credential access, rate limits, trusted dispatch authorization and cost caps operate independent of per-validation tenant choices. Optional checks may be disabled; privileged tool execution must never become authorized solely because a text checker was set to `allow`. `mode=observe` may record recommendations but cannot weaken platform security or leak blocked content.

### Scale and migration

Preserve the 17 legacy `check_<id>` fields through a migration with explicit precedence and one canonical persisted representation; do **not** maintain divergent concurrent authorities. Benchmark DynamoDB item-size limits, record serialization, read/write transaction size and tenant configurations with 1,000+ IDs; use bounded envelopes and pagination for management views as needed. Version catalog/control IDs; unknown formerly enabled controls must fail clearly (or be marked explicitly unavailable/incomplete under controlled policy), never silently pass. Keep data retention and secret material minimal; audit policy revisions and change actor without storing raw prompts. Provide tests for migrating existing tenants, preserving current actions and making repeated migration idempotent.

## Proposed customer API: `POST /v1/interactions`

This is a **proposed endpoint**, not currently implemented. Use existing authenticated `X-GuardOath-Key` or `Authorization: Bearer` conventions where supported by the edge. Requests carry the conversation input plus an approved model identifier (or use a tenant default) and optional bounded request/correlation ID. **Never accept provider secrets, arbitrary egress URLs, privileged authorization evidence or executable policy overrides from an end-user message**. Provider configuration and egress allowlists are managed on trusted tenant/admin infrastructure; support multi-provider adapters with explicit permissions and rate/spend ceilings.

Example request:

```json
{
  "message": "Summarize these notes for my team.",
  "model": "tenant-default",
  "request_id": "request-001"
}
```

Example allowed response (illustrative fields):

```json
{
  "request_id": "request-001",
  "status": "completed",
  "response": "Here is the summary...",
  "input_decision": "allow",
  "output_decision": "allow",
  "findings": [],
  "tenant_policy_revision": 12,
  "model": "tenant-default",
  "usage": {"input_tokens": 24, "output_tokens": 48}
}
```

The gateway should not expose rejected unsafe AI output in the response, errors, logs or partial streaming chunks. Return structured statuses for input blocked, output withheld, redaction completed, provider failure, validation incomplete/timeout, quota/rate limit and transient provider issues. HTTP status mappings must be consistent and documented. Errors should be sanitized. Include safe finding IDs with stage, check version and action, and a correlation/audit ID. Keep usage/billing truthful: distinguish GuardOath validation charges from provider token usage and provider cost.

## Request state machine (no bypass)

1. Authenticate tenant key and retrieve a consistent, immutable tenant policy snapshot for **the whole request**; record revision; verify enabled tenant, quota, budget, provider entitlement and model allowlist.
2. Validate input envelope, encoding and size **before expensive operations**. Resolve enabled input validators by content type/stage and verified tenant settings. Execute under request-level budget and per-check limits. Collect all applicable findings, control errors and transform proposals; apply deterministic tenant action aggregation.
3. If an input block or required incomplete evaluation dictates denial, return an audited safe result **without calling the model**. If redaction is configured, transform supported content and revalidate the transformed message; only send approved transformed input to provider.
4. Call only the tenant-configured permitted AI provider/model with timeout, concurrency, cost, retry and idempotency safeguards. Provider credentials remain server-side; constrain forwarded context, RAG/tool data and total prompt budgets.
5. Buffer model response **before sending bytes to the customer**. Execute enabled output validators on actual model output, with separate stage-specific actions. For agentic tool proposals, intercept and authorize at the trusted dispatch boundary before any tool side effect. Never send unsafe intermediate output or execute a tool prior to authorization.
6. Apply output block/flag/redact/allow; if redact, revalidate; if required validation failed/incomplete, follow safe failure contract. Return only approved output, safe metadata and actual measured usage; record privacy-preserving audit events.
7. Handle errors and retries without accidentally double-charging, duplicating provider calls or exposing the model's rejected output.

### Streaming

**Do not advertise unbuffered token streaming as a safe output-validated gateway.** Initial implementation should be nonstreaming or buffer the *entire* generated output before release; introduce streaming only after a separately tested chunk/window safety and revocation design, acknowledging that already-emitted tokens cannot be recalled.

### Human safety and specialized event inputs

Human-safety emergency assistance must follow clinically reviewed, non-punitive, stage-aware behavior. A user asking for crisis help should not be treated as a malicious attacker by default. SigmaHQ process event rules belong to a separately authenticated **structured-event** pathway and are **never** passed over general chat text. Industry and agentic action validators require trusted host evidence and dispatch boundaries; client booleans are not authorization proof.

## Explicit architectural ownership

**Director (reserved shared files):** own the migration/removal of example profiles from `guardoath/hosted.py`, `tenant_record.py`, `models.py`, `app.py`, `engine.py`, tenant stores and dashboard; decide provider credential storage and egress; wire a secure end-to-end gateway and API documentation. Existing checker-only endpoints may remain but must use the authoritative tenant policy for tenant keys.

**Codex (initial isolated work):** while reserving shared files for Director, write isolated provider-adapter contract, compiled-tenant-plan prototype and end-to-end mock-provider regression tests and API spec. Do not alter shared files until coordination acknowledgement. Defer additional broad Sigma rule expansion if needed to deliver an actual tested gateway vertical slice.

## Acceptance criteria (minimum)

- Single customer API call with mocked permitted provider proves **input → AI → output** ordering and correct content forwarded.
- Blocked or required-incomplete input produces **zero model calls**.
- Redacted input passes through revalidation and provider receives only transformed text.
- Blocked output never appears in API body, trace/error/logs; provider usage still recorded accurately.
- `allow` check runs and produces a reportable finding; `null` check does not execute.
- Two tenants with different enabled controls/actions cannot change one another's decisions.
- Example profiles cannot override tenant controls; former profile request values have a documented rejected/migrated behavior.
- Immutable tenant-policy snapshot holds across both stages even if record changes mid-request.
- Unauthorized tools are denied even if a content validation is set to allow.
- Unknown/malformed controls, missing provider entitlement, unavailable mandatory validators, over-budget runs and model timeout fail safely.
- 1,000+ control-ID configurations are bounded, serialized, retrieved and updated without violating storage limits.
- API authentication, rate/usage limits, provider credential isolation, egress allowlisting, request size limits, input/output stage results, error status contracts and idempotent retry behavior have tests.
- Full CI plus deployment gates still require 12 jobs on exact SHA, Director release acknowledgement/workflow, 15 production checks and cost review before any production cutover.

This document replaces any suggestion that example profiles should remain active or that external callers should orchestrate two separate checks around their own AI call.
