# API validation packs — design contract (proposed)

## Goal
Every authenticated request runs the application's mandatory universal baseline (CORE). An optional array of short codes selects zero or more additional versioned packs, e.g. LOG, AGENT, GEO. A code is a *selector*, never a grant of permission or a way to change the baseline.

## Proposed request
```json
{"stage":"input","content":"Schedule shipment 4821","content_format":"text","validation_codes":["LOG","AGENT","GEO"]}
```
The example is a **future API shape**; the existing CheckRequest and endpoints do not yet accept validation_codes. Preserve canonical content/stage semantics and backward compatibility. Do not embed selectors in untrusted prompt text. Clients pass them as authenticated request metadata. Application defaults can activate packs without request codes.

## Registry
Initial proposed selectors: CORE (implicit mandatory), LOG, HLTH, FIN, AGENT, CYBER, GEO, IOT, WEB3, BIO, AERO, QUANT, MEDIA, CUSTOM. Each selector resolves within a tenant-scoped, administrator-published registry to an immutable pack ID/version, required check IDs, stage applicability, parameter contracts, specialist adapter requirements, and failure behavior. A pack may include multiple domains and a test may belong to multiple packs. CUSTOM resolves only tenant-authorized registered pack IDs; no arbitrary uploaded policy code.

## Resolution and enforcement
1. Authenticate application before resolving selectors; enforce ingress size, count, syntax, duplication and resource budgets.
2. Resolve the mandatory CORE policy and administrator-configured default packs. Resolve requested codes from an allowlist authorized for this application; reject unknown/unauthorized/disabled codes with sanitized error and no downstream AI/tool execution.
3. Compute a deterministic union of check IDs, deduplicate execution, and preserve references to all source packs. Never weaken CORE severity, requiredness, permissions, or failure behavior. Resolve conflicting rules using trusted published precedence and most restrictive effective enforcement.
4. Execute local checks first. Specialist/external adapters run only when policy permits egress and local gates pass. Missing required adapters, timeouts or unsupported formats return incomplete (not passed). For safety-critical cases, preserve raw signals and independent safety controls.
5. Return aggregate decision, individual check statuses, missing/unavailable coverage, executed IDs, pack IDs/versions, policy version, redacted evidence reference, and cost/latency metadata. Explicitly distinguish recommended versus enforced action and observe mode.
6. Reject requests to override role, path sandbox, target domain, credential, external provider or trust anchor using selectors or request_context. Tenant-level defaults and registry changes require administrator authorization and auditable publication.

## API compatibility and tests required before implementation claim
- Existing requests without validation_codes preserve baseline behavior.
- CORE cannot be omitted or disabled; zero, one, multiple and overlapping packs work; same check runs once.
- Unknown, unauthorized, malformed, excessive, case-confusable and version-incompatible codes fail deterministically; no fallback to CORE-only success.
- Different tenants cannot access each other's CUSTOM or privileged packs; codes never grant write/delete tools.
- Both input and output stages honor declared scope; missing specialist adapters yield incomplete with no false pass.
- Tests verify policy immutability, default pack inheritance, fail-closed external routing, evidence versions, stable ordering, resource limits, concurrent requests and backward compatibility.
- Every implemented pack needs real fixtures, documented configuration, dashboard examples, user-guide limitations, CI and coverage mapping to the 200-case catalog.

## Implementation sequence
P0: registry schema and resolver, request model extension, API/engine integration and deterministic offline tests; launch CORE + AGENT + LOG using *only* supported existing validators, explicitly reporting unsupported cases. P1: tenant configuration, admin publication and audit, specialist adapters, pricing/metering and customer self-service. Do not label a catalog use case implemented merely because its pack is selectable.
