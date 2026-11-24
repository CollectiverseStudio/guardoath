# Owner financial operations: continuation design

Recorded November 23, 2026 (America/New_York). This document preserves the next
implementation decisions for #41 and CORE-04 of the [product roadmap](product-roadmap.md).
It is a design, **not an implemented financial API, deployment, or authorization
to operate on a real customer's subscription during development**. The existing
owner reconciliation refreshes saved entitlement; invoice inspection reads
provider information. Neither is cancellation, refund, credit, or plan change.

## First coherent operation: schedule cancellation at period end

Implement this inside the existing owner Billing workspace, authoritative tenant
store and signed audit infrastructure. Do not discover a target subscription:
require an existing server-owned customer/subscription binding. Use the actual
owner/Cognito/MFA verifier and same-origin protection for every operation.

Stripe's pinned update API supports `cancel_at_period_end=true`; immediate
cancellation uses a different operation. A scheduled end must not be presented
as an already canceled subscription or a promise that no further invoice can
exist. Pending prorations, invoice items and usage have separate provider rules.
Sources: [pinned update API](https://docs.stripe.com/api/subscriptions/update?api-version=2025-02-24.acacia),
[cancellation semantics](https://docs.stripe.com/billing/subscriptions/cancel).

Proposed service: `guardoath/admin_subscription_cancellation.py`, integrated
through `admin_runtime.py`, `mount_admin_console()` and `create_admin_router()`.

| Proposed endpoint | Purpose |
| --- | --- |
| `POST /v1/admin/subscriptions/{tenant_id}/cancellation/preview` | Inspect and sign a bounded, short-lived review of the exact saved binding and effective date |
| `POST /v1/admin/subscriptions/{tenant_id}/cancel-at-period-end` | Accept the exact confirmed preview, claim the provider target, persist intent, dispatch once and verify |
| `POST /v1/admin/subscriptions/{tenant_id}/cancellation/receipt` | Recover signed historical intent/outcome; optionally perform a separately labeled current-state read |

The browser supplies tenant ID, expected revision, opaque preview reference,
bounded reason, exact typed confirmation and an action reference. It must never
choose provider IDs, prices, amounts, URLs, raw Stripe parameters or credentials.
Use a confirmation such as `SCHEDULE CANCELLATION usr-...`, with the effective
UTC date displayed before submission. Do not send the operator reason into
Stripe metadata or cancellation comments.

## Verify a narrow supported state before any write

Load a non-demo Stripe tenant, require the exact saved revision and immutable
billing binding, and independently verify a fresh raw provider snapshot. The
read-only subscription reporter is useful for inspection but does not project
all fields required for mutation admission. Validate:

- Exact customer and subscription IDs, configured price and environment.
- A complete single-item supported plan and quantity.
- A known supported active state, automatic collection, explicitly unpaused
  payment collection and no attached subscription schedule or pending update.
- Explicit `cancel_at_period_end=false`, `cancel_at=null`, and a valid future
  period end. Subscription and item period fields must not disagree.
- Unchanged tenant revision, billing target and reviewed provider state on the
  final pre-dispatch read.

Initially refuse unsupported trial/manual-invoice/paused/scheduled states for
this mutation without removing their existing read-only inspection. Sign the
preview with actor, tenant, expected revision, target/request fingerprint,
effective date, operation version and expiry. A current provider state alone
must never create a historical successful action receipt.

## Serialize the target and preserve unknown outcomes

Reuse the signed document and full-document compare-and-set approach in
`DynamoEnrollmentState` (`admin_enrollment_state.py`). Its conditional `PutItem`
pattern fits the audit table's existing GetItem/PutItem permissions. Keep mutable
control documents explicitly separate from immutable signed action receipts.

An action key only prevents replay of that key. Two owners or tabs can generate
different keys for the same subscription, so also require a durable
**per-provider-subscription control record**. Derive its identity from the trusted
provider account scope/environment and subscription, independent of actor,
tenant alias or action key. Key rotation must not make existing claims invisible.

Proposed control states are `RESERVED`, `DISPATCHING`, `VERIFIED` and
`BLOCKED`/indeterminate. Each transition compares the complete verified prior
document and generation. Only the successful transition to `DISPATCHING` may
issue the provider request. Never automatically expire or release a dispatching
or unknown-outcome claim: a delayed worker can still be running. A replaceable
pre-dispatch reservation would require CAS fencing that prevents its former
owner from dispatching; conservative retention is an acceptable first version.

Persist an immutable signed intent before dispatch, binding actor, tenant,
operation/schema version, expected revision, exact request fingerprint, target,
old/new cancellation flags, reviewed date, reason and control generation.
Use a dedicated versioned envelope and reader; do not casually widen the shared
audit signature schema. If implementing an atomic tenant condition plus control
claim, explicitly account for DynamoDB transaction ConditionCheck permissions.
A strong read followed by a separate PutItem is not an atomic tenant CAS.

## One narrow provider dispatch and independent verification

The first adapter should send only the fixed subscription update with
`cancel_at_period_end=true`, using a stable server-derived idempotency key, the
existing pinned API, fixed host, no redirects, strict bounded JSON and bounded
timeout. Do not introduce a generic Stripe mutation proxy. Do not include price,
quantity, invoice/proration, refund, custom cancellation-date or metadata fields.

Do not automatically repeat a dispatch after an ambiguous response. Stripe API
v1's idempotency cache can retain failed responses and can prune keys after at
least 24 hours; a reused pruned key can create a new request. Application intent
and target control therefore remain necessary independently of the provider key.
Source: [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests).

Validate the update response and then independently GET the exact binding.
Require the expected scheduled state and unchanged reviewed period boundary,
plan, environment and identity. Persist the immutable verified outcome before
finalizing the control record. A lost outcome-write response can be recovered
by reading that signed receipt; it must never trigger another provider POST.

Local serialization cannot make the provider read/update atomic against Stripe
Dashboard, the customer portal or another integration. Detect drift and report
uncertainty rather than claiming global provider compare-and-set.

Scheduling must preserve tenant status, entitlement, cap provenance, allowance,
usage, API keys and all customer rules. Trusted subscription reconciliation
remains responsible for entitlement when the provider state actually changes.

| Durable evidence | Allowed result |
| --- | --- |
| No intent | Not found; no provider state inferred |
| Matching intent with no verified outcome | Indeterminate; retain target claim and permit read-only inspection |
| Verified linked intent/outcome | Return historical outcome without provider mutation, even if current state later differs |
| Same key with a different request | Conflict before any provider write |
| Different key for an in-flight/unknown target | Conflict; show safe recovery reference |
| Provider currently scheduled but no action outcome | Current observation only; no invented historical success |
| Prior verified operation later reversed externally | Require fresh preview and confirmation before a new proven generation |

## Visible workflow, credentials and release compatibility

The existing Billing page should show review, effective date, typed confirmation,
reason, action reference, submission state and receipt recovery. Use clear
verified/refused/indeterminate outcomes. Refresh, tenant selection and sign-out
invalidate previews and stale display updates; they cannot cancel a dispatched
server operation or clear its durable claim. An in-flight action's single-flight
guard must survive an unrelated workspace refresh.

Use least-privilege server credentials through the existing secure configuration
mechanism. Current runtime validation accepts `sk_test_`/`sk_live_` and derives
mode from those prefixes; a separate restricted write credential needs a narrow,
explicitly validated configuration path and environment binding. Do not broadly
relax existing credential validation, expose keys, provision a paid service, or
perform live financial actions as part of offline acceptance.

Add a separately versioned persistent operation contract. The current
`state_contracts.release_metadata()` ties introductions to the subscription-cap
writer flag; compute future contract introductions independently so cancellation
cannot accidentally enable cap-provenance writers. Deploy and accept a real
support-only compatibility release before introducing the new operation's
durable state. Keep the actual rollback gate, source parity and all 15 live checks.
Do not infer compatibility from a manifest declaration alone.

## Required implementation acceptance

Complete service, provider adapter, actual authenticated UI/API, durable reopen
and recovery in a coherent batch. Required scenarios include two simultaneous
keys/owners for one target, lost provider response, successful provider action
followed by failed outcome persistence, corrupt/foreign receipts, stale preview,
changed revision/period/price/customer/environment, signed audit outage, exact
provider request body, and byte-for-byte preservation of tenant policy/key/cap/
usage state. Extend the mounted owner browser harness rather than relying only
on intercepted HTTP responses. CI must match the exact published source SHA.

Refunds, credits and plan changes need their own exact-object/amount/currency,
preview, concurrency, reconciliation and recovery contracts; scheduling an end
does not implement them. Provider lifecycle/risk events and a durable processing
ledger also need implementation/evidence before reporting webhook health or
calling financial management complete. Real infrastructure cost ingestion,
freshness and defensible tenant allocation remain separate work.
