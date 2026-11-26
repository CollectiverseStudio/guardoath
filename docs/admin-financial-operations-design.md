# Owner financial operations: implementation and continuation record

Recorded November 23, 2026; implementation updated November 25, 2026
(America/New_York). This preserves the decisions and remaining work for #41 and
CORE-04 of the [product roadmap](product-roadmap.md). Cancellation review,
historical recovery and the independently gated writer are implemented in the
actual owner UI/API. This source enables both `ENABLE_NEW_CANCELLATION_STATE`
and `ENABLE_NEW_SUBSCRIPTION_STATE`, using the already deployed compatible
reader in `762e7b66be029f07d245d9d11dc8a08aed32fd67` as the release baseline.
Scheduling and full reconciliation still require their explicit runtime
prerequisites. The owner workspace explains missing configuration using
nonsecret readiness values; those values cannot grant route or write authority.
Untouched source defaults are exercised through the actual mounted application;
explicit disabled-reader coverage remains. Deployment evidence is recorded in
the [handoff](admin-console-handoff.md). No live financial action or authorization
to operate on real customer subscriptions during development is implied.

Existing reconciliation refreshes entitlement and invoice inspection reads
provider information. Refunds, credits and plan changes remain future work.

## First coherent operation: schedule cancellation at period end

The operation uses the existing owner Billing workspace, authoritative tenant
store and signed audit infrastructure. It does not discover a subscription:
an existing server-owned customer/subscription binding is required. Every route
uses the actual owner/Cognito/MFA verifier and same-origin protection.

Stripe's pinned update API supports `cancel_at_period_end=true`; immediate
cancellation uses a different operation. A scheduled end must not be presented
as an already canceled subscription or a promise that no further invoice can
exist. Pending prorations, invoice items and usage have separate provider rules.
Sources: [pinned update API](https://docs.stripe.com/api/subscriptions/update?api-version=2025-02-24.acacia),
[cancellation semantics](https://docs.stripe.com/billing/subscriptions/cancel).

Implemented service: `guardoath/admin_subscription_cancellation.py`, integrated
through `admin_runtime.py`, `mount_admin_console()` and `create_admin_router()`.

| Endpoint | Purpose and current availability |
| --- | --- |
| `POST /v1/admin/subscriptions/{tenant_id}/cancellation/preview` | Inspect the exact saved binding and encrypt a 180-second review; requires explicit provider configuration and signed audit |
| `POST /v1/admin/subscriptions/{tenant_id}/cancel-at-period-end` | Claim, dispatch once and verify the confirmed review; enabled by source and available only with the explicit provider/audit prerequisites |
| `POST /v1/admin/subscriptions/{tenant_id}/cancellation/receipt` | Read signed historical intent/outcome without any provider request; works with the audit store and no write credential |

The browser supplies tenant ID, expected revision, opaque preview reference,
bounded reason, exact typed confirmation and an action reference. It must never
choose provider IDs, prices, amounts, URLs, raw Stripe parameters or credentials.
The confirmation is exactly `SCHEDULE CANCELLATION <tenant_id>`, with the effective
UTC date displayed before submission. Do not send the operator reason into
Stripe metadata or cancellation comments.

## Verify a narrow supported state before any write

Load a non-demo Stripe tenant, require the exact saved revision and immutable
billing binding, and independently verify a fresh raw provider snapshot. The
read-only subscription reporter is useful for inspection but does not project
all fields required for mutation admission. Validate:

- Exact customer and subscription IDs, configured price and environment, and
  an independently fetched account ID using the same cancellation credential.
- A complete single-item supported plan with quantity exactly one.
- A known supported active state, automatic collection, explicitly unpaused
  payment collection and no attached subscription schedule or pending update.
- Explicit `cancel_at_period_end=false`, `cancel_at=null`, and a valid future
  period end. Subscription and item period fields must not disagree.
- Unchanged tenant revision, billing target and reviewed provider state on the
  final pre-dispatch read.

Unsupported trial/manual-invoice/paused/scheduled states are refused without
removing existing read-only inspection. The AES-GCM preview binds actor, tenant,
exact document hash and revision, provider fingerprint, target, effective date,
operation version, prior control hash and expiry. The prior control hash fences
previews made before a competing completed generation. Expiry is at most 180
seconds and never after the reviewed period end. A current provider observation
cannot create a historical successful action receipt.

## Serialize the target and preserve unknown outcomes

`DynamoCancellationState` uses the existing independent audit table and strict,
domain-separated signed envelopes. Mutable control documents are separate from
immutable signed intent/outcome receipts. All shapes, links, generations,
timestamps and signatures are validated when reopened. Ordinary read auditing
reuses `stripe.subscription` and `audit.receipt`, preserving the preceding
shared audit schema.

An action key only prevents replay of that key. Two owners or tabs can generate
different keys for the same subscription, so also require a durable
**per-provider-subscription control record**. Derive its identity from the trusted
provider account scope/environment and subscription, independent of actor,
tenant alias or action key. Key rotation must not make existing claims invisible.

Control states are `RESERVED`, `DISPATCHING`, `VERIFIED` and `BLOCKED`/indeterminate.
Each transition compares the complete verified prior document and generation.
Only an acknowledged successful transition to `DISPATCHING` may issue the
provider request. A lost transaction response, or later observation of that
state, grants no dispatch authority. Reserved, dispatching and unknown claims
are retained indefinitely; there is no automatic expiry, release or replay.
A later generation requires the prior linked verified outcome and fresh review.

Target reservation and immutable intent insertion commit in one DynamoDB
transaction. An action-reference collision for another target therefore cannot
strand that unrelated target. The intent binds actor, tenant, contract version,
expected revision, exact request fingerprint, target, old/new flags, reviewed
date, reason and generation. Action IDs bind owner and reference independently
of tenant, so changing a tenant cannot silently create another action with the
same reference.

The following dispatch transaction atomically checks the exact tenant document
and revision while changing control to `DISPATCHING`. The deployment policy adds
only `dynamodb:ConditionCheckItem` on the existing tenant table under the existing
admin-mutations condition. No new table, capacity or resource is introduced.
A strong read followed by an unrelated write would not supply this protection.

## One narrow provider dispatch and independent verification

`StripePeriodEndCancellation` sends only the fixed subscription update with
`cancel_at_period_end=true`, using a stable server-derived idempotency key, the
existing pinned API, fixed host, no redirects, strict bounded JSON, a socket
timeout of at most one second and no automatic retry. There is no generic mutation proxy.
The request cannot include price,
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
| Different key for an in-flight/unknown target | Conflict; retain the claim and inspect the original action using its original owner and reference |
| Provider currently scheduled but no action outcome | Current observation only; no invented historical success |
| Prior verified operation later reversed externally | Require fresh preview and confirmation before a new proven generation |

### Execution budget and remaining operational limit

The service uses an eight-second maximum admission/coordination budget. The API
reduces it using trusted Lambda remaining time after owner authorization and
read auditing; browser-supplied budget fields are rejected. At least four
observed seconds must remain before creating the claim and again after acquiring
dispatch ownership. Provider calls receive a deadline leaving a nominal second
for outcome handling. Failure after the claim leaves durable uncertainty and
never repeats POST.

This is not a guaranteed upper bound on execution: blocking database calls and
response body reads can still consume more time before control returns. A slow
or interrupted operation can retain an indeterminate target permanently. This
release has no administrative claim-release or automatic repair operation.
Keep that availability limitation visible. The Lambda timeout remains 15 seconds
and the unrelated PII deadline remains 500 ms.

## Visible workflow, credentials and release compatibility

The existing Billing page shows review, effective date, typed confirmation,
reason, action reference, submission state and receipt recovery. It distinguishes
verified, refused and indeterminate outcomes and independently reads the signed
receipt before displaying verified success. Refresh, tenant selection and sign-out
invalidate previews and stale display updates; they cannot cancel a dispatched
server operation or clear its durable claim. An in-flight action's single-flight
guard must survive an unrelated workspace refresh.

The adapter requires explicit `GUARDOATH_ADMIN_STRIPE_WRITE_KEY` and
`GUARDOATH_ADMIN_STRIPE_ACCOUNT_ID`, alongside the existing configured Stripe
plan/mode and signed audit store. It accepts narrowly validated `rk_test_`,
`rk_live_`, `sk_test_` or `sk_live_` credentials and verifies account identity
through that same credential. Subscription mode must independently match.
Provision only the necessary account/subscription read and subscription update
permissions through the existing secure environment mechanism. There is no
fallback to the existing Stripe key and no relaxation of the read adapter's
credential validation. No key enters the browser or provider metadata. No paid
service or real customer mutation is part of offline acceptance.

`state_contracts.release_metadata()` now computes introductions independently
for `subscription-cap-provenance-v1` and `stripe-period-end-cancellation-v1`.
Both source-controlled financial writer flags are true in this activation
source, alongside the two already enabled catalog flags. Its actual build
manifest therefore introduces all four supported contracts. There is no
environment or deployment-argument override. Cancellation cannot enable
cap-provenance writers or vice versa. The deployed 762e7b6 reader already
supports all four contracts. The release gate must still inspect the actual
downloaded, digest-bound rollback archive before upload; a synthetic compatible
fixture is not production backup evidence. Keep source parity and the remaining
live acceptance requirements; do not repeat a healthy-release audit on every
continuation.

## Implementation acceptance and outstanding release evidence

The service, provider adapter, actual authenticated UI/API, durable reopen and
recovery are integrated in one batch. Repository scenarios include two simultaneous
keys/owners for one target, lost provider response, successful provider action
followed by failed outcome persistence, corrupt/foreign receipts, stale preview,
changed revision/period/price/customer/environment, signed audit outage, exact
provider request body, and byte-for-byte preservation of tenant policy/key/cap/
usage state. The mounted owner browser harness uses real routes and signed-token
verification with synthetic external transports and no HTTP interception.
Focused fault/race browser tests complement that harness.

Historical compatibility acceptance executed locally: 1,590 broad Python 3.12
tests; 254 focused Python 3.13
tests including budget-during-dispatch and trusted Lambda remaining-time cases;
baseline ASGI review with two GETs, scheduling 404 and no financial state;
writer ASGI preview/schedule/recovery with one POST, six GETs, three independently
reopened signed financial records and unchanged tenants. Four signed identity
denial cases executed in each ASGI phase. JavaScript syntax/discovery passed for
42 new focused browser cases and the 10 baseline/8 writer mounted journeys.
Discovery and ASGI execution do not establish Chrome acceptance. All new
exact-SHA jobs, source parity, compatible deployed rollback, real owner/MFA and
15 live acceptance checks remain required. No new test skip or relaxed deadline.

Refunds, credits and plan changes need their own exact-object/amount/currency,
preview, concurrency, reconciliation and recovery contracts; scheduling an end
does not implement them. Provider lifecycle/risk events and a durable processing
ledger also need implementation/evidence before reporting webhook health or
calling financial management complete. Real infrastructure cost ingestion,
freshness and defensible tenant allocation remain separate work.
