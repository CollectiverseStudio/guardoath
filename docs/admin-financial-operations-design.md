# Owner financial operations: implementation and continuation record

Recorded November 23, 2026; implementation updated November 25, 2026
(America/New_York); refund source implementation added November 26, 2026.
This preserves the decisions and remaining work for #41 and
CORE-04 of the [product roadmap](product-roadmap.md). Cancellation review,
historical recovery and the independently gated writer are implemented in the
actual owner UI/API. This source keeps `ENABLE_NEW_CANCELLATION_STATE`
and `ENABLE_NEW_SUBSCRIPTION_STATE` enabled. Their writer phase followed the
compatible reader deployment in `762e7b66be029f07d245d9d11dc8a08aed32fd67`.
Scheduling and full reconciliation still require their explicit runtime
prerequisites. The owner workspace explains missing configuration using
nonsecret readiness values; those values cannot grant route or write authority.
Untouched source defaults are exercised through the actual mounted application;
explicit disabled-reader coverage remains. Deployment evidence is recorded in
the [handoff](admin-console-handoff.md). No live financial action or authorization
to operate on real customer subscriptions during development is implied.

Existing reconciliation refreshes entitlement and invoice inspection reads
provider information. Full refunds of narrowly supported paid invoices now have
their own service, provider, signed-state and owner UI/API implementation,
described below. This writer source sets `ENABLE_NEW_REFUND_STATE=True`.
Publication requires acceptance and actual deployment of the committed reader
`26724c3049db679ded77bfe8ff176e87551835c7` first. The dedicated refund provider
remains unconfigured and requires separate approval. This record does not
establish reader or writer deployment, operational refund availability or
customer financial acceptance. Credits and plan changes remain future work.

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

`state_contracts.release_metadata()` computes introductions from independent
source flags. This writer source enables subscription, cancellation, both catalog
phases and refunds, so its manifest supports and introduces all five contracts:
`subscription-cap-provenance-v1`, `stripe-period-end-cancellation-v1`,
`catalog-publication-v1`, `catalog-publication-lifecycle-v1` and
`stripe-invoice-refund-v1`. There is no environment or deployment-argument
override. One operation's flag cannot activate another operation's writer.

The earlier 762e7b6 deployment established reader support for the four preceding
contracts, not refund compatibility. Reader source
`26724c3049db679ded77bfe8ff176e87551835c7` supports all five while introducing only
those four, with its refund flag false. Its accepted CI and actual deployment
are required before this writer is published. The release gate must inspect the
real downloaded, digest-bound rollback archive before writer upload; a committed
reader, synthetic compatible fixture or candidate used as its own backup is
insufficient. Actual release evidence belongs in the
[handoff](admin-console-handoff.md). Keep source parity and the remaining live
acceptance requirements; do not repeat a healthy-release audit on every
continuation.

## Cancellation implementation acceptance and outstanding release evidence

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

## Full paid-invoice refunds: writer source

The separate implementation uses `admin_invoice_refund.py`,
`admin_refund_provider.py`, `admin_refund_state.py`, opaque selections from
`admin_invoices.py`, and the actual owner Billing routes and browser. Its contract
is `stripe-invoice-refund-v1`. Current writer source sets
`ENABLE_NEW_REFUND_STATE=True` and introduces that contract as well as supporting
its signed readers. Explicit disabled-reader compatibility remains covered with
the flag false. The dedicated refund provider is still absent: review,
submission and provider refresh require its separately approved configuration.
Signed history only needs the retained audit store and signing key. This section
does not claim reader/writer deployment, a configured refund credential or
customer financial acceptance.

### Supported payment and authority

The server starts with an existing non-demo tenant's saved Stripe customer and
subscription binding. The selected invoice must be paid, collected automatically
for subscription creation or renewal, and contain exactly one complete,
nonprorated subscription line at quantity one and the configured recurring price.
Invoice, PaymentIntent and captured charge must establish the same account,
mode, customer, invoice, payment and currency. The PaymentIntent must have
succeeded and identify that settled charge.

The refundable amount is a positive integer derived from Stripe, at most
99,999,999 minor units. Invoice amount due, amount paid and total, PaymentIntent
amount and amount received, and charge amount and amount captured must agree.
The invoice must have zero remaining amount, no customer-balance or credit-note
funding, and no out-of-band payment. Any previous refund, including a pending or
failed one, blocks a new review. Disputes, manual collection, prorations,
multiple or ambiguous payment shapes, Connect transfers/fees and incomplete
provider evidence are excluded. There is no partial-refund amount input,
credit-note operation, plan change or subscription cancellation in this flow.

Display uses an explicit currency map. Unsupported currencies refuse review
before financial state is reserved; there is no inferred currency exponent.

| Fractional digits for Stripe charge/refund units | Supported currencies |
| --- | --- |
| 0 | JPY, KRW |
| 2 | AED, ARS, AUD, BRL, CAD, CHF, COP, CZK, DKK, EUR, GBP, HKD, HUF, IDR, ILS, INR, ISK, MXN, MYR, NOK, NZD, PHP, PLN, RON, RUB, SEK, SGD, THB, TWD, UGX, USD, ZAR |

The integer minor-unit amount is displayed alongside the formatted amount and
included in the exact confirmation. HUF, TWD, ISK and UGX follow the charge/refund
units used by the implementation. Sources:
[pinned invoice object](https://docs.stripe.com/api/invoices/object?api-version=2025-02-24.acacia),
[pinned PaymentIntent object](https://docs.stripe.com/api/payment_intents/object?api-version=2025-02-24.acacia),
[pinned charge object](https://docs.stripe.com/api/charges/object?api-version=2025-02-24.acacia),
[Stripe currency rules](https://docs.stripe.com/currencies#special-cases).

### Review, submission and recovery routes

Every route requires verified owner/MFA authority, same-origin admission and a
persisted protected-read audit attempt. Provider identifiers and raw responses
remain private to the service and signed financial records. The browser receives
opaque references, fingerprints, amount, currency, mode and bounded results.

| Endpoint | Behavior and availability |
| --- | --- |
| `POST /v1/admin/subscriptions/{tenant_id}/refund/preview` | Open the actor/tenant/revision-bound opaque invoice selection, inspect the payment and issue an encrypted 180-second review; requires the dedicated provider configuration and signed audit store |
| `POST /v1/admin/subscriptions/{tenant_id}/refund` | Reinspect the exact review, reserve the charge, dispatch once and independently verify; source enabled, mounted only with explicit provider/audit prerequisites and absent in the disabled-reader phase |
| `POST /v1/admin/subscriptions/{tenant_id}/refund/receipt` | Reopen signed historical intent/outcome without any Stripe request; requires the audit store and signing key, not the refund credential |
| `POST /v1/admin/subscriptions/{tenant_id}/refund/refresh` | Explicitly read Stripe for a retained unresolved action and append a valid later observation; source enabled with provider/audit prerequisites, absent in the disabled-reader phase, and never issues a Stripe POST |

Invoice selections expire after 900 seconds and grant no mutation authority.
The separate review binds the actor, tenant, exact saved document/revision,
provider snapshot, charge target, prior control hash and expiry. Submission
requires that review, a bounded reason, action reference and exactly
`REFUND <tenant_id> <amount_minor> <CURRENCY> MINOR UNITS`. The service derives all
provider IDs, mode, amount and currency. The reason stays in the signed intent;
it is not passed to Stripe as a refund reason or comment.

### Permanent claim, one POST and independently proven outcome

The target digest binds provider account, mode and charge, independently of
owner, tenant alias, current plan price or credential rotation. Its claim is
permanent: there is no successor generation after success, failure or an unknown
outcome. The intent and initial control are inserted together. A second
transaction compares the exact saved tenant document/revision while acquiring
dispatch authority. Only an acknowledged transition to `DISPATCHING` permits the
one provider request; observing that state later never permits another POST.

The adapter pins `2025-02-24.acacia` and sends only `charge`, the full `amount`,
and server-generated `metadata[guardoath_action]` to `POST /v1/refunds`, with
`guardoath-ir1-<action_id>` as the idempotency key. It uses a fixed host, rejects
redirects, bounds JSON and request timeouts, and never retries the POST. The
service uses the existing maximum eight-second coordination budget, reduced by
trusted Lambda remaining time. Expiry, lost acknowledgements, timeouts or
termination after reservation retain the claim. Blocking external calls can
still outlast the observed budget; this is not a guaranteed completion deadline.
Source: [pinned refund creation](https://docs.stripe.com/api/refunds/create?api-version=2025-02-24.acacia).

Independent account, refund and charge reads must confirm the same immutable
binding, full amount, currency, action metadata and refund identity. `succeeded`
is verified only when the independent charge also reports the entire amount
refunded. `pending` and `requires_action` remain pending; `failed` and `canceled`
remain failed. Unsupported, malformed or conflicting proof remains unavailable
or indeterminate. The signed intent, outcome and bounded observation chain are
revalidated on every reopen, including exact proof identity and monotonic
progress. Repeated unchanged pending reads do not append duplicate observations.

When a POST response is lost, explicit refresh uses a bounded charge-filtered
refund list, requires one exact action-bound match, independently retrieves it,
and verifies the charge. Missing, multiple or foreign matches cannot establish
success. Provider calls during refresh are GET-only; appending a signed outcome
is a separate audit-table write. There is no automatic polling, replacement
refund, claim release or repair endpoint. Verified and failed terminal receipts
are returned without another provider read.

Receipt lookup survives removal of the refund provider and ordinary tenant
revision or configured-price changes. It still needs the original owner and
reference, retained signing key and intact signed records. Explicit provider
refresh can also use the original canonical price after a plan-price change,
provided account and mode still match; every historical payment field remains
bound to the intent. New review and dispatch continue to require the current
configured price. No refund changes tenant entitlement, access, allowances,
cap provenance, usage, keys or customer rules.

### Dedicated credential and separate release activation

The dedicated refund provider is not configured. Provisioning and installing its
credential require separate approval; the existing cancellation credential and
its earlier authorization do not supply refund access.

Runtime configuration is the pair `GUARDOATH_ADMIN_STRIPE_REFUND_KEY` and
`GUARDOATH_ADMIN_STRIPE_REFUND_ACCOUNT_ID`, alongside the existing Stripe
reader/price/mode and signed audit configuration. Partial configuration fails
initialization. The refund credential must differ from both the reader secret
and the cancellation credential; the cancellation key and its permissions remain
unchanged. There is no credential fallback. To retain receipt-only operation
after removing refund provider access, remove both refund variables and retain
the audit store and its signing key.

Use these minimal Stripe Dashboard restricted-key permissions:

| Resource | Permission |
| --- | --- |
| Accounts | Read |
| Invoices | Read |
| Payment Intents | Read |
| Charges and Refunds | Write |
| All other resources and all Connect permissions | None |

Stripe groups Charges and Refunds together. Write includes read access and also
permits charge mutations at the provider-permission level; the key is not
refund-only. GuardOath's adapter constrains its own mutation to the exact refund
POST. Sources: [Stripe permission groups](https://docs.stripe.com/stripe-apps/reference/permissions),
[restricted-key behavior](https://docs.stripe.com/keys/restricted-api-keys).

Reader source `26724c3049db679ded77bfe8ff176e87551835c7` is the required
compatibility baseline. It supports `stripe-invoice-refund-v1` with
`ENABLE_NEW_REFUND_STATE=False`, introduces no refund state, leaves submission
and refresh unmounted, and refuses financial state writes and provider POSTs.
Preview remains possible there with explicit provider configuration, and signed
receipt readers preserve later claims after rollback. Keep this disabled-reader
behavior in explicit compatibility acceptance alongside current writer defaults.

This separate writer source sets `ENABLE_NEW_REFUND_STATE=True`, making all
five contracts introduced. The reader's required acceptance and actual deployment
completed on November 26 at 12:03 p.m. Eastern and are recorded in the handoff.
Writer deployment requires the real, digest-bound rollback archive
to support all five contracts. Neither reader commitment nor writer preparation
establishes that deployed rollback evidence. No environment variable or
deployment argument overrides the phase, and the other financial/catalog flags
cannot substitute for the refund flag. The absent dedicated provider keeps
refund operations unavailable even with the writer source enabled; configuring
it remains a separate approved step. Source activation, deployment and customer
financial acceptance are distinct evidence requirements.

Further refund scope, credits and plan changes remain separate work. Provider
lifecycle/risk events and a durable processing ledger also need implementation
and evidence before reporting webhook health or calling financial management
complete. Real infrastructure cost ingestion, freshness and defensible tenant
allocation remain separate work. This refund implementation does not complete
CORE-04.
