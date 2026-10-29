# Agentic safety — UC101–UC110 executable acceptance plan

Status: **requirements and fixture plan**, not implemented coverage. All tool permissions and execution guards must be enforced by the host application or trusted gateway, never delegated to the model's text alone. Tests below must be exercised against the real API and tool-dispatch boundary before marking a row verified.

| ID | Boundary and required integration | Attack/invalid case | Legitimate control | Failure/edge case |
|---|---|---|---|---|
| UC101 | Proposed-tool schema checker plus trusted dispatch gate | Extra `recipient`, path traversal, or amount over approved limit in proposed tool JSON | Allowed tool with exact typed fields and bounded values | Duplicate JSON keys, nested over-budget objects, schema unavailable; no dispatch |
| UC102 | Trusted per-run step/depth counters in agent runtime | Self-spawning tasks exceed maximum depth or total steps | Bounded delegated workflow completes | Concurrent child tasks, cancellation, replayed run IDs; stop without orphan execution |
| UC103 | Contextual probe classifier and scoped tool policy | User asks agent to enumerate secrets or environment outside its task | Authorized diagnostic request from approved operator | Quoted examples, harmless `whoami` discussion, classifier outage; no keyword-only authorization |
| UC104 | Server-owned workflow state machine | `ship_order` before verified payment event | Paid order proceeds through allowed transition | Stale state, duplicate webhook, race between payment and shipping; atomic compare-and-swap |
| UC105 | Authenticated identity/role and tool-level authorization | Read-only principal attempts `delete_order` via prompt | Read-only lookup succeeds; authorized writer can update | Missing identity, forged role in prompt, expired token, cross-tenant resource; deny |
| UC106 | Trusted message provenance and structured context boundaries | User embeds fake `system` or `tool_result` markers claiming completion | Legitimate quoted logs and markdown are treated as data | Unicode controls, conflicting transcript segments; preserve provenance and flag ambiguity |
| UC107 | Untrusted third-party response isolation and injection screening | API response instructs agent to exfiltrate secrets or change system instructions | API response returns ordinary shipment data | HTML/JSON encoding tricks, truncated response, parser failure; never promote response to instructions |
| UC108 | OS/container sandbox and trusted canonical path allowlist | Agent writes through symlink or `../` outside workspace | Approved file inside workspace | TOCTOU symlink swap, alternate mount, invalid path encoding; OS-level denial |
| UC109 | Trusted token, call, spend and time budgets at dispatch | Recursive agent exceeds per-run cost or call budget | Within-budget task completes | Parallel calls, provider price change, token estimation error; reserve budget atomically |
| UC110 | Server-configured host/domain allowlist with network egress enforcement | Agent attempts redirect, DNS rebinding or localhost/private-IP request | HTTPS request to approved destination | IDNA confusion, CNAME to private IP, redirect chain, DNS change; recheck at connection |

## Implementation batches

1. **Deterministic boundaries first:** UC101 tool arguments, UC104 state transitions, UC105 authorization, UC108 filesystem and UC110 network allowlists. Only UC101 currently has a narrow existing proposed-tool-call validator; it does not authorize or execute.
2. **Runtime controls:** UC102 and UC109 require trusted, concurrency-safe runtime counters and billing limits. Existing process-level API request budgets do not satisfy them.
3. **Context and response trust:** UC103, UC106, UC107 require reviewed datasets and provenance-preserving orchestration, plus false-positive calibration.

## Acceptance and evidence

Each implementation must show: trusted policy source; input and output stages where applicable; observed and enforced action; fail-safe on missing dependencies; correct tenant isolation; positive, adversarial and boundary fixtures; independent dispatch denial tests; sanitized evidence with policy/checker version; and CI success. Never mark an item verified from a simulated checker, a prompt-only test, or a test that does not reach the actual enforcement boundary.

## Implemented primitive — not integrated into agent dispatch

`guardoath.agent_budgets.AgentBudget` now provides bounded, thread-safe in-process reservations for per-run steps, depth and call counts. Reservations are atomic and do not refund usage after a failed downstream call. `tests/test_agent_budgets.py` covers boundaries, invalid values and concurrent contention. This is a **partial foundation** for UC102 and UC109, not a complete implementation: it is not yet connected to the API, persistent run identity, agent runtime, billing/token costs, distributed coordination, or cancellation of in-flight work. No claim of preventing autonomous loops or cost overruns is made until those boundaries are enforced and verified.

## Additional deterministic primitives (pending CI verification)

- `guardoath.workflow_guard` provides a strict, server-configured transition graph and deterministic allow/deny decisions for UC104. It rejects shipping directly from `created` in the illustrative workflow. **It does not own the database state, verify payment or perform atomic compare-and-swap**, so full UC104 enforcement is still outstanding.
- `guardoath.agent_authorization` provides a tenant-scoped permission decision for UC105. Identity and grants must be authenticated by the integrating application, and actual tool dispatch must independently enforce authorization. **It does not authenticate users or prevent a host from bypassing it.**
- Unit tests cover valid/invalid transitions, cross-tenant isolation, least privilege and missing identity. These are deterministic foundation tests, not proof of full agent orchestration security.

## Pre-dispatch composition (new; CI verification pending)

`guardoath.agent_dispatch_gate.AgentDispatchGate` now composes the existing tenant permission decision, workflow transition guard and atomic in-process budget reservation. A denied permission or invalid transition consumes no budget. A successful assessment reserves one step and one call before a host attempts a tool action. `tests/test_agent_dispatch_gate.py` covers authorized, unauthorized, invalid transition, depth and concurrent budget-exhaustion scenarios.

**Security boundary:** This is an opt-in library primitive, not a production enforcement gateway. It does not authenticate the principal, validate the tool's JSON arguments, verify durable workflow state, dispatch tools, implement database atomicity, or coordinate across replicas. The returned `approved_for_host_recheck` explicitly requires the integrating host to revalidate trusted state and permissions at execution. Never treat this return value as an independent authorization token or a guarantee of safety.
