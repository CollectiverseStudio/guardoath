# Isolated tenant-authoritative AI gateway prototype

This package provides an executable, nonstreaming **test ASGI application**, not
an endpoint registered in `guardoath.app` or deployed to guardoath.com. It calls
only the host-injected provider adapter. Tests use a fake provider and make no
paid model calls. Shared Director-owned application/engine/tenant/store/UI/CI
files are read/used without modification.

`api.create_prototype_app(gateway, authenticate)` exposes a separate
`POST /v1/interactions` taking only `message`, `model` and `request_id`. The host
supplies an authenticator returning the actual validated `TenantRecord` and a
provider binding for that same tenant. The API accepts the existing key header
or Bearer convention, rejects client policy/profile/validation-code/provider/tool
parameters, sanitizes invalid envelopes, and bounds incoming body bytes. There
is no arbitrary egress URL or credential input and no live provider implementation.

`PrototypeGateway` copies and revalidates the authenticated record, invokes its
**existing `to_policy()` compiler**, and uses the **existing real `Engine`** for
both stages. There is no competing action map, profile resolver or catalog policy
authority. Tenant revision, compiled-policy digest, catalog version and safe
checker/action/version metadata accompany the result. Findings never expose
checker explanations or rejected model text.

Input block/incomplete/simulated work prevents the provider call. Supported PII
redaction uses the engine's transform/revalidation before forwarding; output is
buffered, checked and redacted/revalidated before release. `allow` still runs and
reports findings, `flag` reports, and disabled fields do not run. All tool proposals
are withheld: this prototype has no tool executor, and content `allow` is never
authorization for a side effect.

Bindings are immutable and tenant-owned, require an async adapter, and define a
model allowlist, entitlement and bounded in-memory provider-call allowance. A
shared request deadline covers queueing and execution; provider timeout is also
capped by remaining request time. Cooperative async adapters are required: Python
cannot preempt arbitrary CPU-bound adapter code. Production needs isolated
adapter execution and independently enforced outbound/network timeouts as needed.

The prototype serializes interactions through one async lock; it is not a
production concurrency/rate limiter. A bounded 128-entry, process-local cache uses
a keyed request fingerprint, stores only final safe envelopes, and prevents
in-process duplicate provider calls/retries, including safe cancellation envelopes
after a started provider attempt. Cancellation still propagates to the cancelled
caller; a same-ID retry receives the stored failure and never restarts that attempt.
Replays require the same current tenant revision, compiled-policy digest and
catalog version, retain original usage and set `replayed=true`. A changed policy
or same-tenant same-ID different body conflicts without returning cached output
or repeating the provider call. Thus the cache cannot override newly stored
tenant actions. Provider error/timeout usage remains unknown (`null`), not fabricated.
This is **not durable idempotency, a billing ledger, quota reservation in tenant
storage, credential storage, a real provider adapter, or a cross-process service**.
Cache eviction/restarts require a production retry/retention design. Approved
response text is retained in this bounded cache; prompts/rejected output are not.

Existing example-profile tenants and observe-mode records are refused with an
explicit configuration error rather than silently changing their behavior.
Director must implement the approved persisted migration and hosted profile
removal. JSON-only checks, content-type applicability and richer stage-specific
configuration retain current engine/record behavior; this package does not modify
or pretend to complete those shared-file migrations.

## Catalog and migration contracts

`catalog.catalog_for()` derives the 17 source-backed IDs, stage/action capabilities
and implementation version metadata from the real tenant/checker contracts. The
catalog describes capabilities and never selects what runs. Sigma event controls
are absent and never applied to chat content.

`migration_preview()` and `update_preview()` are **pure, nonpersisting experiments**
for a bounded canonical map on a future tenant record. They preserve off/allow and
disabled settings, require known IDs and supported stages/actions, bind owner and
revision, reject duplicate JSON keys and retain catalog/source revision. Repeated
migration is idempotent. The preview is never accepted by `PrototypeGateway` or
compiled into an alternate runtime policy. Nested settings now reject unknown
keys and use the real Policy schema and Engine configuration validation, even
when disabled. Source defaults normalize without mutating the submitted map.
Persisted tenant schema changes and storage transactions remain Director-owned.

Synthetic schema tests at 1,000, 1,500 and 2,048 controls measure serialization and
updates under a 350,000-byte envelope. These are **not 2,048 published checks** or
a DynamoDB integration/load benchmark. Exact item overhead, configuration schema,
metadata versions, entitlements and management pagination need production review.

## Verified and remaining

Tests cover actual input → provider → output ordering, source-backed policy
selection, all finding actions, absent controls, unavailable checks, transforms,
output buffering/privacy, two-tenant isolation, policy changes mid-request,
profile/pack/owner overrides, provider entitlements/call bounds, timeouts, retry
conflicts and concurrent duplicates, tool withholding, strict envelopes, a real
local PII checker, and migration/scale contracts.

Director retains hosted endpoint registration, tenant/profile/map migration,
storage/credential/egress/identity integration, durable idempotency and billing,
rate/concurrency controls, public response/status decisions, and deployment. No
AWS resources, real model calls or production deployment were performed. Existing
release/cost gates remain mandatory.

Run: `pytest tests/test_gateway_prototype.py tests/test_gateway_catalog_prototype.py -q`.

Director's denial contract: completed checks return HTTP 200 even when
`status` is `input_blocked`, `output_withheld` or `tool_withheld`.
Clients must inspect `status`, not just HTTP success. `X-GuardOath-Decision`
mirrors the result status. Blocked input makes zero provider calls; withheld
output is absent. Error results retain non-2xx statuses.

The canonical migration preview preserves tenant `rule_actions` as a separate
map. Registered rule IDs, literal actions and PII-redaction restrictions use the
persisted TenantRecord validator; unknown or malformed entries fail without
returning submitted values. Older previews without this optional map normalize
to an explicit empty map. Control updates retain it and reject incompatible
redaction changes. This is a pure preview, not a settings persistence endpoint.
