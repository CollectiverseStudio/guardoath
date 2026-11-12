# GuardOath AI and Agent Activity System of Record
**Authoritative product contract — 2026-11-11. Proposed architecture; NOT an implemented event ledger.**

## Non-negotiable provider independence

The customer chooses the AI model/provider and the agent/executor. GuardOath MUST NOT require, select, substitute, or silently route to a specific model, agent framework, tool provider or vendor. Any supported integration conforms to one provider-neutral transaction schema and the customer's authoritative tenant policy. No external AI or paid service is required to process a deterministic check.

GuardOath acts as the **source of record for transactions it intercepts**, and for events a trusted customer integration reliably attests. It cannot honestly certify calls that bypass its gateway. Mark events from a customer-reported integration as **reported**, not **gateway-observed**; missing externally reported calls are *not known absent*.

## Transaction / event model

A transaction is an append-only stream of privacy-safe event envelopes. Separate the immutable event envelope from optional restricted content storage.

| Field | Contract |
| --- | --- |
| `tenant_id`, `application_id`, `transaction_id` | Server-authenticated tenant boundary; transaction ID generated server-side. Never trust AI-provided tenant identity |
| `event_id`, `sequence`, `occurred_at`, `recorded_at` | Unique monotonically ordered, tenant-scoped event sequence; separately record source time and server receipt time |
| `parent_transaction_id`, `correlation_id`, `span_id` | Trace model request, AI response, proposed agent call, execution and receipt without merging unrelated tenants |
| `event_type` | request_admitted, input_checked, model_dispatched, model_completed, output_checked, action_proposed, action_checked, approval_requested, approval_granted, approval_denied, executor_dispatched, executor_completed, executor_failed, executor_outcome_unknown, transaction_completed or transaction_blocked |
| `event_origin`, `evidence_level` | Gateway-observed vs signed host-reported; unverified events clearly labeled and forbidden from asserting confirmed dispatch or success |
| `model_provider`, `model_id`, `agent_provider`, `agent_id`, `executor_id` | Optional opaque customer-selected identifiers, never a baked-in vendor dependency |
| `policy_revision`, `checks`, `decision` | Authoritative tenant revision, bounded rule IDs/status, all findings and allow/flag/block/redact/incomplete outcomes, without raw PII |
| `action_digest`, `approval_digest`, `idempotency_key_digest` | Hashes of canonical exact proposed action and authorization context; no raw arguments or secret-bearing tokens |
| `usage`, `usage_source` | Reported token counts, provider-reported cost/currency and observed durations; do not invent usage, rates or invoice totals |
| `attempt_id`, `outcome`, `error_code` | Distinguish approved, dispatched, acknowledged, succeeded, failed, timed out, blocked and UNKNOWN; an HTTP acknowledgment is not completion |
| `prev_digest`, `event_digest`, `schema_version` | Canonical event hash chain, versioned schema and verification metadata. Protect integrity against ordinary edits; stronger non-repudiation requires a separately trusted signer |

Do **not** include prompt text, AI replies, file bytes, passwords, authorization headers, plaintext keys, internal host reference values, unredacted tool arguments or provider-secret bodies in the default ledger. Content retention requires explicit tenant authorization, purpose-specific encryption, separate restricted storage, retention deadline and deletion policy.

## Trust and state boundaries

1. **Inbound:** server authenticates tenant and application, assigns a transaction ID and records admission. Failed authentication is a bounded security event, not a trusted tenant transaction.
2. **Input decision:** apply the existing shared GuardOath Engine with the authoritative tenant policy. If Block, incomplete or mandatory attachment-inspection failure, record a blocked decision and **never** dispatch the model. Capture all findings, without exposing sensitive values.
3. **Model dispatch:** customer-selected provider/model is supplied by the authenticated integration; persist a dispatch-attempt event. A lost response means **outcome unknown**, never assumed success. Provider usage is labeled provider-reported versus locally measured.
4. **Output decision:** check the model response with the same tenant policy before delivery; preserve original and redacted-response verification states as distinct events.
5. **Agent proposal:** an AI suggestion has no execution authority. Log the proposed action digest but do not infer agent execution.
6. **Approval and dispatch:** after onboarding/billing completion, the optional execution gateway binds exact operation, destination, arguments digest, tenant, principal, policy revision, expiry and executor version in a host-verified approval. GuardOath must revalidate before dispatch. Invalid/altered/replayed claims are blocked with **zero side effects**.
7. **Execution result:** record dispatched, accepted/acknowledged and definitively succeeded as different states. Timeout or lost response is **unknown**; retry only with proven idempotency. Never convert incomplete to allowed.
8. **Ledger durability:** persist a decision/dispatch intent before side effects, append outcome with optimistic concurrency or atomic conditional writes, and expose stable reconciliation status. Record-write failure must prevent fresh external execution; an outcome-write failure requires reconciliation, not a fictitious success.
9. **Read access:** customers see only their authenticated tenant's bounded timeline, filters and receipt metadata through the existing dashboard/API. Session authority governs management; subscription entitlement governs paid operations. Cross-tenant lookup and user-supplied event-ID guessing must fail closed.

## Acceptance scenarios before calling this a system of record

| ID | Test | Expected |
| --- | --- | --- |
| LEDGER-001 | Two different customer-selected AI providers emit same canonical events | Identical GuardOath event contract, no preferred provider |
| LEDGER-002 | MCP and Lambda adapters emit equivalent outcome envelopes | One schema; differing executor adapters cannot bypass policy |
| LEDGER-003 | Model Input is blocked | Recorded decision, **zero model dispatches** |
| LEDGER-004 | Output blocked or required redaction recheck incomplete | No output release; complete lineage retained without raw content |
| LEDGER-005 | AI proposes tool call without registered authorized capability | Proposal recorded, **zero execution attempts** |
| LEDGER-006 | Customer changes operation, args, destination or tenant after approval | Digest mismatch; deny; no dispatch |
| LEDGER-007 | Duplicate request, retry, delayed response, out-of-order event | Stable idempotency, ordered receipts, no duplicate side effects |
| LEDGER-008 | Executor returns HTTP 202 then disappears | Dispatched/acknowledged/**unknown**, never succeeded |
| LEDGER-009 | Ledger write unavailable before execution | Fail closed; no external side effect |
| LEDGER-010 | Final receipt persist fails after a side effect | Reconciliation-required and uncertainty visible; no invented success |
| LEDGER-011 | Tenant A requests tenant B's transaction ID | Deny without metadata/content leakage |
| LEDGER-012 | Signed host report versus untrusted client assertion | Accurate provenance; unverified report cannot claim witnessed execution |
| LEDGER-013 | Missing token usage or provider cost | Null/unknown, never fabricated or billed as confirmed |
| LEDGER-014 | Tampered or missing hash-chain event | Verification failure and gap surfaced, not silently accepted |
| LEDGER-015 | Tenant retention deletion/export request | Policy-governed content removal/export with audit evidence and legal hold controls |
| LEDGER-016 | Real authenticated dashboard views transaction timeline | Existing tenant record/revisions, pagination, search, receipt state and privacy controls accurate |

## Delivery order and dependencies

**Current priority remains completed, deployed, end-to-end account sign-in/registration and verified paid subscription (#36/#37), then release safety (#30/#32).** Do not start paid/side-effecting executor calls before these gates. After that: (1) tenant Action Executor Registry, (2) MCP and Lambda adapters, (3) exact-action approval Execution Gateway, (4) integrated durable Activity Ledger and receipts. The **provider-neutral transaction/receipt schema is designed up front** so existing input/output API integrations can eventually append the same events without a migration into an unrelated audit silo.

A product-complete claim requires actual persistence, tenant-authorized API and dashboard, source-to-receipt lineage, 13 Tests jobs, three CodeQL jobs, strict evidence as relevant, privacy/reliability acceptance, 15 live deployment checks and sampled end-to-end reconciliation. This document records acceptance requirements only; **no execution gateway, complete traffic ledger, or universal call interception is claimed to exist today**.
