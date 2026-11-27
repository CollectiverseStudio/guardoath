# GuardOath operator administration

The new `/admin` route redirects to `/admin/login` only when the admin feature is enabled. It is **disabled by default**. Ordinary customer API keys and Cognito sessions never grant owner privileges.

## Authorization
Set `GUARDOATH_ADMIN_ENABLED=1` only after establishing a separate public Cognito admin OAuth client with PKCE and callback `https://guardoath.com/admin/login`, in the existing trusted pool, and verifying independently fetched `GuardOathOwners` membership and signed current-login MFA evidence. Required: `GUARDOATH_ADMIN_CLIENT_ID`, `GUARDOATH_ADMIN_COGNITO_DOMAIN`, `GUARDOATH_ADMIN_CURSOR_HMAC_KEY` (independent 32–64 random bytes, hex). An invalid/missing configuration fails closed at startup. Each authorization request independently checks a complete Cognito group page: malformed, duplicate, oversized or paginated memberships (including an empty continuation token) deny owner access even if an owner group appears in the response. Distinct additional groups remain permitted. This changes no roles or enrollment. CloudFront forwards `X-GuardOath-Admin-Session`; `Authorization` is reserved for AWS origin signing.

## Capabilities

This section describes current source capabilities. Historical implementation
notes below record earlier stages and do not supersede this summary. Source and
offline acceptance are not evidence of AWS deployment or live financial data.

| Area | Current implementation | Remaining acceptance or capability |
| --- | --- | --- |
| Owner access | Separate Cognito client, signed tokens, fresh independent owner membership/account checks, recent MFA, same-origin mutations, memory-only browser tokens | Actual configured production login and all live acceptance |
| User management | Bounded masked directory/exact lookup; confirmed Cognito enable/disable with signed intent and independently verified outcome, recoverable ambiguity | Live provider acceptance; no owner role grants or self-disable |
| Tenant management | Existing tenant store, suspend/reactivate, key revocation, stage-specific rule actions, downward request caps, revision checks and signed atomic audit | Deployed customer safeguards and source parity |
| Subscriptions | Provider inspection, cap-preserving reconciliation, protective delinquency downgrade and reviewed saved-cap restoration with signed history; eligible owners can restore the verified plan allowance | Live Stripe acceptance and provider plan changes remain open |
| Billing and costs | Bounded invoice history, usage/allowance history, request-cap signals, confirmed period-end cancellation and deployed full paid-invoice refund submission/recovery; dedicated test-mode refund setup completed November 26 | Customer financial acceptance; credits, infrastructure cost/revenue allocation and lifecycle/risk-event processing remain open |
| Catalog | Shared registry, signed description drafts/reviews, reversible metadata source plans and an enabled publication/CI/deployment/history workflow | An operator must run a requested release; live publication and general behavior/schema authoring remain open |

`GUARDOATH_ADMIN_AUDIT_TABLE` is required in production for protected reads and
mutations. The separate audit table and scoped permissions must already be
approved and provisioned; configuring a variable is not evidence that they exist.
Tenant writes require typed confirmation, reason, expected revision and an
atomic tenant-update/audit transaction. Cognito writes persist a signed intent
before the provider action and record its independently checked outcome.

### Inspect published checks and rules

Open **Catalog**, expand a category and check, and select **Inspect check** or
**Inspect rule**. The inspector reads the same published registry and the real
`TenantRecord` setting schema. It shows the category description, check outcome,
use case and limitations, Input/Output eligibility, actions, positive examples,
benign look-alikes, known false positives/misses, and declared implementation,
unit-test and evaluation paths. Rule details also show content format, required
settings and prerequisite checks. A prerequisite may belong to another check;
the displayed schema remains restricted to the selected check's fields.

These examples are published source content, never a tenant's submitted values.
They appear as escaped JSON strings, so newlines, invisible characters and
directional controls remain visible. HTML-like examples and schema references
are rendered only as text; inspection does not run a detector, follow a URL,
modify a draft or activate a tenant check. Account, payer, billing, usage and key
fields are excluded. Data-shape schemas do not replace runtime model validators,
host-signed reference verification, stage eligibility or saved Off settings.

The two authenticated reads are:

- `GET /v1/admin/catalog/categories/{category_id}/checks/{check_id}`
- `GET /v1/admin/catalog/categories/{category_id}/checks/{check_id}/rules/{rule_id}`

Both require `expected_registry_version`, current verified owner/MFA authority
and, in production, a persisted signed `catalog.registry` read-attempt receipt
before registry access. The response is bound to the exact category/check/rule
and version. Unknown targets return 404, changed versions return 409, and
malformed source/schema or unavailable audit returns a sanitized 503. Selecting
another item, refreshing or signing out invalidates late responses. Inspection
does not change the existing inventory projection, signed-draft fingerprints or
source-history chain.

Declared file paths are not executed test receipts. The inspector explicitly
leaves current-source CI and live deployment unverified, and reports an unknown
detector version as unavailable. Catalog publication/rollback and behavior/schema
authoring remain separate unfinished capabilities under #42.

### Provider-verified subscription reconciliation

This source enables full owner reconciliation and cap/plan provenance through
`ENABLE_NEW_SUBSCRIPTION_STATE=True`. Runtime availability also requires the
existing signed audit store, Stripe reader/price and an explicit positive paid
plan allowance. The Billing workspace identifies missing prerequisites without
revealing credentials or treating configuration status as write authority.

The owner **Reconcile subscription** action calls
`POST /v1/admin/subscriptions/{tenant_id}/reconcile`. The backend accepts only
an expected tenant revision, typed `RECONCILE <tenant-id>` confirmation, reason
and idempotency key. Owner identity comes from authenticated Cognito/MFA, and
customer/subscription/price/environment/plan allowance come from the saved record
and trusted server configuration. Browser claims cannot grant paid access.

The service fetches the existing saved subscription directly, or discovers one
unambiguous subscription for the saved customer and independently fetches it
again. Active admission requires the configured price/environment, exactly one
item at quantity one, explicit unpaused collection and `charge_automatically`.
Missing collection information and manual-invoice subscriptions cannot activate
this automatic-payment plan. A known nonactive status revokes quota; a positive
quantity change does not obstruct that conservative revocation. These rules are
shared with customer reconciliation. See Stripe's
[subscription lifecycle](https://docs.stripe.com/billing/subscriptions/overview)
and [collection methods](https://docs.stripe.com/billing/collection-method).
This is entitlement reconciliation, not a receipt proving money was collected.

`GUARDOATH_PAID_MONTHLY_ALLOWANCE` must explicitly configure a positive plan
allowance before the runtime exposes full owner reconciliation. Saved
`admin_monthly_allowance_cap` and `stripe_plan_monthly_allowance` separate an
operator restriction from the nominal provider plan. Both are server-controlled,
not customer-editable. Delinquency sets effective allowance to zero without
erasing a known cap; verified recovery still honors that cap. Account suspension,
keys, usage, checks, Input/Output Off choices and unrelated tenants remain intact.
Legacy bound records without provenance retain their saved restriction,
including zero. An unbound legacy account with a positive blocking allowance
also retains that restriction through customer binding and first activation.
An audited cap-restoration workflow remains separate future work.

A byte-exact source compare-and-set and revision condition protect the update.
The signed `billing.reconcile` receipt and tenant change commit together. Its
idempotency key is bound to actor, tenant and the exact confirmed request. After
an ambiguous response, **Retry same reconciliation** uses the same payload/key
and recovers the persisted signed result without another provider call or write.
Reusing a key with different parameters is refused. Replays describe the
historical operation; they do not claim to be current Stripe state. The UI also
reads and validates the signed receipt before reporting success. A failed
subsequent workspace refresh does not erase that verified outcome.

The UI's **Request cap** is measured in validation requests. The legacy API
feature name `monthly_cost_caps` does not mean dollars, AWS cost or provider spend.

### Full paid-invoice refunds: writer source

The owner Billing workspace implements refund review, full-amount submission,
signed history and explicit provider refresh. Current writer source sets
`ENABLE_NEW_REFUND_STATE=True`. Submission, review and refresh require the
dedicated provider, which was separately approved and configured in test mode
on November 26. History remains available with the signed audit store. Actual
reader/writer acceptance, deployment and configuration are recorded in the
[handoff](admin-console-handoff.md). Customer financial acceptance remains open;
this does not complete CORE-04. Explicit compatibility coverage retains the disabled-reader
phase with `ENABLE_NEW_REFUND_STATE=False`.

The supported target is an existing non-demo tenant's paid subscription creation
or renewal invoice, collected automatically, with one nonprorated configured
recurring-price line at quantity one and one settled, undisputed charge. The
server independently checks the invoice, PaymentIntent, charge and refund list.
The full paid amount must match throughout. Previous refunds, including pending
or failed attempts, customer-credit funding, credit notes, out-of-band payments,
manual collection, multiple/ambiguous payments and Connect transfers/fees are
excluded. There is no partial-amount entry or credit operation. Refunding an
invoice preserves the subscription, tenant access, request caps, cap provenance,
usage, keys and rules; cancellation is a separate control.

**Owner workflow**

1. Choose **Invoices** beside the tenant, then **Review refund** beside a paid
   invoice. The selection is an opaque reference valid for 15 minutes; it grants
   no refund permission. The server derives the exact account, mode, customer,
   invoice, charge, amount and currency. An unsupported or changed payment is
   refused before submission.
2. Review the tenant, invoice date, test/live mode, full amount, integer minor
   units and UTC expiry. The separate refund review lasts 180 seconds. After
   writer deployment and approved provider configuration, enter the exact displayed
   `REFUND <tenant_id> <amount_minor> <CURRENCY> MINOR UNITS` confirmation and a
   10–240 character reason, then choose **Refund reviewed full amount**. The
   reason is retained in signed history and is not sent to Stripe. The browser
   cannot select another amount or send provider IDs or arbitrary Stripe fields.
3. Retain the tenant ID and action reference shown before submission. The
   service reserves a permanent charge claim and sends at most one refund POST
   after fresh provider inspection and an acknowledged exact-tenant transaction.
   The browser reopens signed history before reporting the recorded result.
   Signing out or changing selection cannot cancel a server operation.
4. For recovery, sign in as the original owner and enter that tenant ID and
   reference under **Refund history and status**. **Inspect refund history**
   reads stored signed evidence without contacting Stripe. With the writer and
   provider configured, **Refresh refund status** reads Stripe for an unresolved retained
   action and may append a signed observation. It uses only Stripe GET requests;
   there is no polling or repeat refund POST.

The API operations are preview, submit, receipt and refresh under
`/v1/admin/subscriptions/{tenant_id}/refund`; the
[financial operations design](admin-financial-operations-design.md#review-submission-and-recovery-routes)
records their exact routes and boundaries. Each uses verified owner/MFA,
same-origin admission and signed protected-read auditing. Financial provider
identifiers remain private; the UI shows references and fingerprints.

| Result | Meaning and next step |
| --- | --- |
| Verified | Stripe reported `succeeded` and an independent charge read confirmed the entire amount refunded. Signed history records that observation time. |
| Pending | Stripe reported `pending` or `requires_action`. Retain the reference; use an explicit status refresh when available. |
| Failed | Stripe reported `failed` or `canceled`. The permanent charge claim remains; this workflow sends no replacement refund. |
| Indeterminate | The action has no authoritative final result. Retain the reference and inspect history or explicitly refresh; the held claim never expires automatically. |
| Not found | No signed intent was found for this owner/reference. This does not establish Stripe's current state or authorize a refund retry. |

Lost-response recovery requires one exact action-metadata match in a bounded
charge-filtered refund list, followed by independent refund and charge reads.
Missing, multiple or conflicting results cannot become verified success. A new
action reference, owner or credential rotation cannot bypass the charge claim.
No claim-release or automatic repair endpoint exists. Verified and failed
terminal records return their signed history without another provider read.

Historical receipts survive ordinary tenant revision changes, plan-price changes
and removal of the refund provider, provided the signed audit store and original
signing key remain available. Explicit refresh also accepts the original signed
price after a plan change while requiring the same configured account/mode and
exact payment binding. New review and submission still require the current plan
price. Provider refresh requires a configured credential and the writer phase;
receipt lookup requires neither.

Amounts are bounded to 99,999,999 integer minor units and use this explicit
display map. Other currencies refuse review; a currency's display exponent is
never guessed.

| Fractional digits for Stripe charge/refund units | Supported currencies |
| --- | --- |
| 0 | JPY, KRW |
| 2 | AED, ARS, AUD, BRL, CAD, CHF, COP, CZK, DKK, EUR, GBP, HKD, HUF, IDR, ILS, INR, ISK, MXN, MYR, NOK, NZD, PHP, PLN, RON, RUB, SEK, SGD, THB, TWD, UGX, USD, ZAR |

HUF, TWD, ISK and UGX use Stripe's charge/refund units. The exact integer remains
visible beside the formatted amount. See [Stripe currency rules](https://docs.stripe.com/currencies#special-cases).

**Dedicated refund configuration**

The dedicated refund provider was separately approved and configured on November
26 at 2:08 p.m. Eastern. The cancellation key remains separate. This completed
setup does not need to be recreated during subsequent feature work.

Configure the server-only pair `GUARDOATH_ADMIN_STRIPE_REFUND_KEY` and
`GUARDOATH_ADMIN_STRIPE_REFUND_ACCOUNT_ID` through the existing secure environment
mechanism. Runtime also requires the existing Stripe reader, configured price
and matching mode, and the independent signed audit store. A partial pair fails
initialization. The refund key must differ from the reader key and from
`GUARDOATH_ADMIN_STRIPE_WRITE_KEY`, which remains the separate cancellation key
with its existing permissions. There is no fallback to either credential and no
key is returned to the browser. Remove both refund variables when withdrawing
refund provider access; keep the audit store/signing key for receipt recovery.

Use a separate Stripe Dashboard restricted key with these minimum permissions:

| Resource | Permission |
| --- | --- |
| Accounts | Read |
| Invoices | Read |
| Payment Intents | Read |
| Charges and Refunds | Write |
| All other resources and all Connect permissions | None |

Stripe's combined **Charges and Refunds** Write permission includes reading and
also permits charge mutations. It is not a provider-level refund-only key; the
GuardOath adapter restricts its own writes to the exact full-refund endpoint and
pins `2025-02-24.acacia`. Sources:
[permission groups](https://docs.stripe.com/stripe-apps/reference/permissions),
[restricted-key permissions](https://docs.stripe.com/keys/restricted-api-keys).

This writer source enables `ENABLE_NEW_REFUND_STATE=True`. Reader
`26724c3049db679ded77bfe8ff176e87551835c7` passed required acceptance and was
actually deployed on November 26 at 12:03 p.m. Eastern. That reader supports all five
contracts but introduces only the four preceding contracts, with refund writers
disabled. The actual reader release receipt is in the
[handoff](admin-console-handoff.md), and writer
deployment must verify the real downloaded rollback archive against its deployed
digest and all five required contracts.

Retain explicit disabled-reader compatibility: with the refund flag false,
submission/refresh routes remain unmounted, provider POSTs and financial state
writes are refused, and later signed history remains readable. No environment
variable bypasses the phase. Existing cancellation, subscription and catalog
writer settings cannot substitute for the refund flag or the dedicated
credential. The existing eight-second coordination budget and trusted Lambda
remaining-time checks limit admission; blocking calls or termination can still
leave a permanent indeterminate claim. Writer source, reader/writer deployment,
credential configuration and customer financial acceptance remain distinct.

### Restore a saved local request cap

In **Tenants**, select **Review cap restoration** for the account. The review
shows the saved request cap, configured plan allowance, current tenant status,
verified subscription period, any ordinary scheduled cancellation and expiry.
The label describes the saved local cap; historical reconciliation may have
inferred it, so it is not proof of a particular previous owner action.

The tenant must have coherent saved cap/plan fields and a currently active
supported subscription. Missing provenance or a changed configured plan needs
**Reconcile subscription** first. The service independently retrieves the saved
subscription during review and again before applying the change. It uses the
existing read credential; no new Stripe key is required.

Enter the
exact displayed `RESTORE <tenant_id> TO <plan_allowance> REQUESTS PER MONTH`
confirmation and a reason, then choose **Restore plan allowance**. The browser
retains the original action reference before sending. The server restores only
the configured allowance and removes the saved cap; suspension, usage, keys,
validation settings, subscription status and overage blocking stay unchanged.
A disabled account remains disabled.

The review expires within 180 seconds or at the current period end, whichever
comes first. Changed tenant data, configuration or provider proof require a
fresh review. Success requires the signed saved receipt and next revision.
After a connection error or an unverified response, retain the original action
reference and inspect signed history. A **Not found** result does not prove a
pending request cannot finish and does not unlock a replacement action.
Historical inspection never calls Stripe or reapplies an old allowance; it
remains available with the audit configuration after provider removal or a
compatible source-disabled rollback.

The preview, apply and receipt paths are respectively
`POST /v1/admin/tenants/{tenant_id}/allowance-cap/restore/preview`,
`POST /v1/admin/tenants/{tenant_id}/allowance-cap/restore`, and
`POST /v1/admin/tenants/{tenant_id}/allowance-cap/restore/receipt`.
Each requires verified owner access, fresh MFA, same origin and a signed read
audit. The request accepts no client allowance, plan, environment or provider ID.
See the [financial design](admin-financial-operations-design.md) for the exact
transaction, replay and rollback contracts.

### Persistent-state rollback compatibility

Non-null cap/plan provenance cannot be read by older application images that
forbid unknown tenant fields, and deleting it would remove a safeguard. Ordinary
records with neither field omit the absent fields; populated values, including
zero, are always preserved.

Lambda builds declare supported and introduced persistent-state contracts
in their generated source manifest. Source `9881ddb` supports and introduces all six contracts.
The deployed reader `9f44394` supports all six and introduces the previous five,
so it can read restoration receipts during rollback.
Reader `26724c3049db679ded77bfe8ff176e87551835c7` supports all five with its
refund writer disabled; its required acceptance and actual deployment are
recorded in the handoff. The earlier four-contract 762e7b6 reader
alone cannot satisfy the refund writer's rollback requirement. Before any Lambda update,
`Deploy-Verified.ps1` verifies the downloaded rollback archive against the
actual deployed `CodeSha256`, checks the candidate's exact source SHA, requires
the rollback image to support every introduced contract, and refuses loss of
already-deployed support. Archive inspection does not extract or execute code.
Rollback rechecks the same backup digest immediately before restoration.

Rollback authority is also bound to the update that this deployment actually
performed. The script hashes the candidate archive, validates the
`UpdateFunctionCode` response and retains that response's revision. It verifies
the candidate digest, revision and successful update after the waiter. If a
later deployment or configuration update changes the revision, automatic rollback
refuses to overwrite it, even when the code bytes happen to match.

Restoration requires the current candidate digest and the exact retained revision
to match, and the update to be terminal. The conditional restore uses that
retained revision; it never adopts a newer observed revision or retries a
revision conflict. A lost or malformed original update response leaves ownership
uncertain and does not authorize an automatic restore. An update still in
progress is also refused. If the backup digest is already present, the script
reports an observation and performs no restore write. A completed restoration
must independently verify the backup digest, returned new revision and successful
update state before it is reported restored. All 15 live checks still apply.
These rules use AWS's documented [conditional code updates](https://docs.aws.amazon.com/lambda/latest/api/API_UpdateFunctionCode.html)
and [update states](https://docs.aws.amazon.com/lambda/latest/dg/functions-states.html).

The offline PowerShell acceptance runs the actual production helper functions
against scripted AWS responses and real backup archives. It tests concurrent
updates, ambiguous responses, incomplete updates, digest/revision mismatch,
restore verification and archive tampering. It does not execute AWS actions or
establish that a production rollback has occurred.

The preceding compatibility baseline set source-controlled
`ENABLE_NEW_SUBSCRIPTION_STATE=False`; explicit rollback tests retain that phase.
That same phase controls generated artifact metadata and all producers: owner
full reconciliation, request-cap actions, both tenant stores' subscription updates
and creation of records carrying provenance. There is no production environment
override or builder switch. Existing populated fields and signed receipts remain
readable, preserved and enforced, making this baseline capable of interpreting
later writer data after rollback.

Legacy customer activation and positive request caps remain usable without adding
new fields. A legacy bound account that loses paid entitlement keeps zero on
recovery until a later audited restoration can distinguish its prior restriction.
An unbound zero-cap mutation is refused during the baseline when no provenance
exists: otherwise it would be indistinguishable from a newly registered account
awaiting its first subscription. The API explains this limitation and points to
the existing **Suspend** control. Bound zero caps and existing explicit zero caps
remain supported. Historical unbound zero records cannot reveal whether an old
operator cap existed; this source does not claim to reconstruct that history.

For the existing subscription/cancellation activation, the compatible reader
was already deployed as 762e7b6. That activation enables the corresponding new
state and full owner reconciliation independently of the refund phase. The
actual downloaded archive must be the digest-verified compatible backup before the update, and the
same gate rejects a legacy rollback. Do not fabricate a manifest, strip saved
provenance, use the candidate as its own rollback or bypass the gate. Offline
fixtures do not establish the identity of the production backup.

### Offline acceptance

`playwright.admin-baseline.config.js` exercises the explicit disabled-reader phase with
real owner login, persisted legacy-shaped request caps, explicit unbound-zero
refusal/suspension, unavailable future-writer route and mobile navigation.
`playwright.admin.config.js` exercises full reconciliation and cancellation under
the untouched source defaults in the isolated test application.
Both run actual browser HTML/JS against the mounted
GuardOath application, RSA-signed Cognito token validation, owner/MFA verifier,
tenant adapter, mutation services and signed audit readers. SQLite persists the
typed DynamoDB boundary across independent process reads. External Cognito,
Stripe and DynamoDB boundaries are explicitly synthetic. Separate browser
failure tests cover ambiguous retries, altered receipts, rejected requests,
session loss and post-success refresh failure. Existing browser tests remain in
the CI gate. This evidence does not replace live AWS/Cognito/Stripe acceptance.

Refund writer acceptance must exercise the untouched true source default with
synthetic provider configuration, the absent-provider runtime, independently
reopened signed outcomes and GET-only recovery. Explicit false-reader acceptance
must continue to preserve later signed records while refusing submission,
refresh observations and provider POSTs. These remain release requirements;
writer preparation does not claim that acceptance or deployment has completed.

## Release gate
Run full repo and browser tests plus exact-SHA Tests (13), CodeQL (3), Field Comparisons Evidence (2), Trusted Reference Acceptance (1), PII Reliability Gate (3), Stripe Reconciliation Acceptance (2), and Deploy Script Acceptance (1). All 25 individual jobs must succeed. Maintain the 500-ms checker limit, verified rollback compatibility and all 15 live acceptance checks. Unresolved issue #30 must be reported, never silently closed. A GitHub commit is not an AWS deployment.

## Historical implementation notes

The following sections retain the implementation history. Earlier statements
that a later-implemented capability is outstanding describe that earlier stage;
the current capability summary above governs current source status.

## Opt-in infrastructure provisioning
The deployment template includes default-off `EnableAdminConsole` and a separate `AdminCursorHmacKey` NoEcho parameter. Only explicitly enabling the feature provisions an isolated Cognito OAuth code/PKCE client, the `GuardOathOwners` group, and pool-scoped read-only Cognito IAM permissions. The template rejects activation without an independent random 32–64 byte hex key. It creates no owner account or group assignment and does not provision a mutation audit table.

**Do not activate from a source commit.** First verify stack change set and costs, the existing SOFTWARE_TOKEN_MFA policy, an explicitly enrolled authorized owner, signed `amr` evidence from the **current MFA login** (not mere MFA enrollment), CloudFront forwarding `X-GuardOath-Admin-Session`, exact-source CI, rollback and all 15 live acceptance checks. If Cognito does not emit the expected signed per-login `amr`, the verifier correctly denies access: do not weaken it. Until this evidence is available, leave `EnableAdminConsole=0`.


## Cognito user directory
The owner-only UI and \`GET /v1/admin/users\` page existing Cognito users through the server identity service. The \`email\`-prefix filter and page size (1–50) are bounded. Continuation tokens are HMAC-signed and bound to pool, owner identity, exact query, limit and a 15-minute expiry; raw owner subjects and email-prefix filters are replaced with separate keyed digests, not exposed in base64 cursor payloads. Invalid/expired or legacy cursors are refused before a provider read. Only masked email hints, account confirmation/enabled flags, recognized status and UTC creation/last-modified timestamps leave the server. Total count is unknown, not zero; provider/IAM failures, corrupt user status or identity attributes, invalid provider pagination and oversized provider pages return a sanitized HTTP 503 (unavailable), not a misleading client-input 422. Invalid owner filters, limits and signed cursors still return 422 before any Cognito read. One provider call is made per page. This is a read-only UI; it neither alters Cognito identity nor authorizes tenant access or payment, and is not a substitute for persistent owner-read audit or future user writes. No new paid services or AWS resources are introduced.

## Persistent audit infrastructure (opt-in, default disabled)
`EnableAdminMutations=0` creates **no** additional resources. Explicitly setting it to `1` requires `EnableAdminConsole=1` and provisions a **separate** DynamoDB `AdminAuditTable` with 1 provisioned read and 1 write capacity unit, stable `event_id` partition key, deletion protection and Retain policies. CloudFormation rejects this configuration when the existing tenant table is at 25 RCUs or WCUs. This option consumes additional AWS capacity: do not enable without reviewing the stack change set, current account free allowance, cost budget and deletion/restore procedure.

The conditional Lambda environment variable `GUARDOATH_ADMIN_AUDIT_TABLE` and permissions limited to the dedicated audit table activate the signed audit pathways. Genuine audit receipts remain immutable and use conditional append. First-authenticator enrollment, described below, also uses explicitly separate signed **control records** in that table; those records are mutable through conditional `PutItem` with a full-document compare-and-set. The runtime does not use `UpdateItem`, `DeleteItem` or table scans for this workflow. The table alone grants no owner membership, Cognito account creation, refund or catalog publishing capability. Replaying transactions, partial provider failures and independent read-access auditing require separate acceptance before exposing more operations.

## MFA provenance and sign-in freshness
Owner authorization requires a **signed** Cognito access-token `amr` array containing the explicit `mfa` marker, plus an integer signed `auth_time` no older than 15 minutes (30 seconds of forward clock skew). A single `otp`, `totp` or `software_token` marker is **not** proof of multi-factor sign-in. Refresh tokens retain the original `auth_time`, so a refreshed token cannot renew the original 15-minute administrative authentication window. The owner group, enabled account, subject and live token validity are independently checked on every request.

**Current AWS configuration limit:** The existing `UserPoolTier=LITE` and `UserPoolDomain.ManagedLoginVersion=1` use Cognito's classic hosted UI, whose default claims are unsuitable for this strict MFA check. The separate native password + `SOFTWARE_TOKEN_MFA` API flow can receive signed MFA claims without requesting paid-tier step-up. AWS documents a gradual rollout, so a real signed access token from the actual pool must still prove the required claims before activation. Requesting a target step-up level or custom ACR configuration is separate and requires Essentials/Plus; this feature requests neither. Do not synthesize `amr`, accept enrollment as proof of sign-in, or change the customer pool's tier or hosted UI. See [AWS authentication-level documentation](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-step-up-authentication.html).

## Directory identifier privacy
Provider usernames that are not email addresses are now displayed as stable, key-dependent opaque `user-...` labels rather than unmasked Cognito usernames or arbitrary truncated identifiers. The labels are HMAC-SHA256-derived with a dedicated domain separator and do not grant account-selection or mutation privileges. Email-like usernames continue to use masked email hints. The raw provider usernames remain server-side, and the signing secret is never returned to the browser.

## Subscription item quantity verification
The owner read-only subscription reporter now rejects missing, zero, negative, noninteger or excessive Stripe item quantities, even if Stripe reports the subscription as active. Valid positive quantities preserve the independent provider-price/environment/customer and persisted tenant checks. Rejected provider snapshots are unavailable, never converted to a paid entitlement. No Stripe mutation or billing charge is performed.

## Per-tenant API-key inventory (read-only, redacted)
The authenticated tenant directory and tenant detail now report `api_key_count` from the authoritative saved tenant's key-hash list, including the production DynamoDB paged projection. Only the number of issued API keys (0–2) is emitted; no key hashes, tokens or Stripe bindings are returned. Missing/malformed legacy key metadata is reported as unavailable rather than a fabricated zero. This is an inventory count, not a key-usage metric and does not change revocation, billing or access.

## Audited owner action revision receipts
Admin rule-action edits and downward allowance caps now persist the prior and next tenant revisions in the same atomic audit record as the before/after changed values. This aligns the existing status-action audit with rule and cap operations. The authoritative tenant record is updated conditionally or neither mutation nor audit insertion succeeds. The audit event contains no raw tenant document, Stripe credentials or customer API keys.


## Operator action receipts in the browser
Following a confirmed tenant suspend/reactivate, rule-action change, or downward allowance-cap mutation, the authenticated owner UI validates the response tenant ID, monotonic next revision, and 64-character audit event ID. A malformed or missing receipt is shown as an unverified change rather than falsely reporting success. Successful responses display the audit event identifier and saved revision for operator reconciliation. The backend continues to require an atomic tenant-write/audit-write transaction; this UI check never substitutes for server authorization, an audit read, or deployment verification.

## Owner-scoped directory pagination
Cognito user-list pagination cursors are now version 3 and bind the signing pool, keyed email-prefix digest, page limit, expiration **and a keyed digest of the independently verified administrator subject**. A cursor issued under one authorized owner cannot be replayed by another owner; mismatches fail locally before another Cognito read. The server derives the owner subject from its trusted Cognito/MFA verification, not browser query parameters. Old cursors fail closed. The browser-visible cursor is signed but not encrypted: its payload contains only non-sensitive scope, separate keyed owner/filter digests, and the opaque Cognito continuation token. Previously issued version-2 cursors are invalidated, not silently reinterpreted. Missing or duplicate Cognito email/email-verification attributes cannot masquerade as verified account metadata.


## Owner-scoped tenant pagination and unknown allowances
Tenant-directory HMAC-signed continuation tokens use version 2, binding the independently verified Cognito owner (via keyed pseudonym), table, exact page limit, search and 15-minute expiry. Other-owner replay, changed limit/filter, expired/empty/forged/legacy cursor tokens fail before any further DynamoDB scan. Only the server selects the current owner from the verified session, never a browser query. Invalid or missing legacy monthly allowance is shown as **Unavailable** rather than manufactured zero, and the browser suppresses cap changes when the stored allowance is unknown. No authoritative tenant data is altered by these read-only projections.

## Owner-only persistent audit receipt inspection (opt-in)

When both `EnableAdminConsole=1` and `EnableAdminMutations=1` are explicitly enabled, successful tenant suspension/reactivation, tenant rule-action changes and downward request-cap changes atomically persist an immutable audit event alongside the tenant revision in the dedicated retained, deletion-protected audit table. A verified owner can now inspect one event by its 64-character ID using the authenticated /admin Audit Receipt panel or `GET /v1/admin/audit/{event_id}`. Each lookup uses a single, strongly consistent DynamoDB GetItem and an explicit allowlist projection; it is **not** a Scan or log enumeration. The same verified Cognito owner and recent MFA authorization is enforced server-side as other /admin APIs. A missing ID produces 404, invalid ID 422 and malformed row/provider outage 503. Only validated actor, tenant, UTC time, redacted before/after, revisions and reason are returned. No API keys, billing bindings, raw tenant settings, export, deletion, or Stripe operations are available. IAM adds only audit-table GetItem to existing conditional PutItem without allocating more capacity. This feature is a receipt inspection workflow, not a complete searchable audit ledger, and is unavailable without the opt-in.

## Verified subscription reconciliation triage (read-only)

The authenticated owner **Verify Stripe** workflow now returns and displays a redacted reconciliation assessment from the same direct Stripe snapshot and authoritative saved tenant record. It marks a saved active Stripe entitlement with an inactive/unverified provider binding as `critical`; an active provider whose saved tenant entitlement is inactive, billing mode conflicts, or tenant account is disabled as `review`; matching provider/saved states as `consistent`; and unbound inactive records as `unavailable`. Every result retains its source and UTC read timestamp only when a real provider read occurred. The UI provides next-step guidance, never simulates AWS costs or invoice amounts, never grants paid access, and never writes Stripe, billing or tenant state. Provider errors remain 503. The existing audited tenant suspension is a separate operator action. Persistent read audit and signed-MFA production activation remain pending.

The reconciliation label is never inferred from Stripe status alone. For a verified **non-active** subscription, a saved `active` status remains **critical** even if the customer account is already disabled; a different non-active stored status is marked **review**, not misleading `states_agree`. Provider states `unpaid`, `incomplete`, `incomplete_expired`, `trialing`, and `paused` intentionally map to saved `inactive`; matching `past_due` and `canceled` also count as agreement. This diagnostic operates only on an already provider-verified snapshot; it does not change reconciled entitlements, issue refunds, invoke provider mutation, or prove subscription revenue.

## Exact Cognito enrollment inspection (read-only)

The authenticated Cognito Users panel also accepts a **known exact email** for one bounded account-state inspection. The browser transmits the email only in a same-origin POST body to `/v1/admin/users/lookup`, never a URL query; clears the input after submission and clears the redacted result on sign-out. The backend derives the owner subject exclusively from the independently verified, current MFA-enabled Cognito session. It makes a single Cognito `ListUsers` request with an exact equality email filter and `Limit=2`, then verifies the returned identity's email attribute matches exactly (case-insensitive). Zero matches are explicitly *not found*, ambiguous/inconsistent provider data and outages fail closed as unavailable, and malformed/filter-injection input is refused before any provider call. The response contains only a masked user label/email, status, enabled and verified flags, dates and source/freshness; raw usernames, full email, private attributes and credentials are not returned. This adds no IAM permission or resource beyond the existing opt-in read-only directory; **it cannot suspend a Cognito identity, grant subscription access, or replace audited user writes**. Operator account-state inspection remains read-only; durable read auditing and real Cognito user mutation are outstanding requirements.

If Cognito supplies a continuation token during an exact lookup, the result is refused instead of claiming a unique identity from a partial page. Missing email-verification metadata remains `Unavailable`, not an invented negative response.

Exact-email inspection is additionally constrained by a conservative per-process, per-owner 20-request/60-second sliding window (HTTP 429 before any additional Cognito call), with a bounded number of owner windows. This is **not** a distributed/global rate limiter across Lambda instances. Production-wide rate limits and persistent read-audit remain outstanding; do not represent this local safety control as closing issue #40 or #41.

## Tenant quota signal (recorded usage, not billed cost)
The authenticated owner **Usage** action now displays an on-demand month-to-date quota signal from the same authoritative tenant usage counter, saved monthly allowance and overage policy returned by `/v1/admin/tenants/{tenant_id}/usage`. Signals are **unavailable** (no counter), **within limit** (below 80%), **watch** (80% to below 95%), **near limit** (95% to below 100%), **at limit** (100%) and **over limit** (above saved allowance). Percentage is integer-floored and unavailable for a zero allowance, while the at/over limit distinction still works. A record with `allow_and_record` is never presented as blocking. These are read-time advisories, not persistent alarms or actual enforcement decisions; a monthly aggregate cannot infer a daily spend forecast. No AWS costs, Stripe charges, invoices, or margins are invented or retrieved and no paid provider calls are introduced.


## Tamper-evident administrative audit receipts
New tenant-status, rule-action and downward allowance-cap audit events are sealed inside the existing atomic DynamoDB tenant/audit transaction with keyed HMAC-SHA256 over the allowlisted typed receipt fields. The independent server audit key is derived from the owner admin HMAC secret and never returned to the browser. The authenticated lookup validates the signature (revisions, reason, change, actor) before displaying **HMAC integrity verified**. Signed receipts with changed projected fields, invalid signatures or unavailable verification keys fail closed. Existing pre-upgrade receipts lack signatures and are explicitly labeled **Legacy receipt — integrity unverified**, not cryptographically verified; legacy fallback is not a tamper-proof ledger. This is a limited integrity improvement, not a searchable ledger, an all-read audit, or production verification. No tenant policies, Stripe state, new IAM permissions, provider spending or runtime release flags changed.

## Versioned catalog description preflight (owner-only; no publishing)
The authenticated Categories and checks panel has a structured **Preview description** action for each published rule. It sends the expected current registry version, a candidate next version, stable category/check/rule IDs, a proposed title and use case to `POST /v1/admin/catalog/preview`. The backend verifies the current canonical control registry and refuses stale, missing, ambiguous, malformed or no-op proposals. MFA-confirmed owner sessions and same-origin are mandatory. The response is a bounded before/after diff indicating **preview_only, persisted=false, published=false**. It does not create a durable draft, run detector tests, modify customer tenant settings, publish category/check/rule definitions, or grant host-signed reference authority. A later draft/review/test/publish/rollback pipeline is still required for issue #42. Treat a successful preflight strictly as a proposed metadata review, not a release.

## Verified subscription period and cancellation signal
The owner-only **Verify Stripe** action uses the existing trusted, bound subscription read to show the provider's current-period end (UTC), when a valid Unix timestamp is actually returned, and whether Stripe explicitly marks cancellation at period end. Missing or malformed optional provider fields are **Unavailable**, not invented dates or booleans. This is not an invoice amount, a guaranteed renewal, a charge forecast, or a manually granted entitlement. The page explicitly labels next invoice amount unavailable. No extra Stripe request, price mutation, provider write, billing credential exposure, or change to tenant entitlement is introduced. Provider binding, plan price, environment and quantity checks still run first.

## Shared Cognito directory provider-call budget (defense in depth)
Both owner directory pagination and exact-email inspection now use one bounded **20 Cognito ListUsers calls per verified owner per rolling 60-second window per warm application instance**, rather than separately allowing unlimited pages. Filter, page limit, verified-subject and signed cursor validation run **before** consuming the budget; a rejected request never reaches Cognito. Exceeding the budget returns HTTP 429 from either endpoint before a provider request. The authenticated UI names the limit, clears potentially stale user-list rows and disables the continuation control on a failed page. This does not alter user identity, confirmation, entitlement, or billing. This in-memory limit is neither a cross-instance distributed quota nor a persistent audit trail; both remain open production requirements.


## Consistent admin POST origin checks
The authenticated owner-only exact-account lookup, catalog preview, tenant status, tenant rule-action and allowance-cap POST endpoints share one Origin verifier. Production accepts only `https://guardoath.com`. Local browser integration accepts exact `http://localhost:<port>` and `http://127.0.0.1:<port>` origins with port 1–65535. Missing, multiple, foreign, spoofed, malformed and out-of-range origins fail HTTP 403 before a provider or tenant mutation. The verifier is defense in depth: Cognito owner/MFA, session checks, audit and versioned tenant constraints remain mandatory.


## Local-only admin Origin exception hardening
All five owner-protected administrative POST routes accept `https://guardoath.com` as the production Origin. A `http://localhost:<port>` or `http://127.0.0.1:<port>` Origin is accepted **only** when the actual ASGI listener and target request URL use exactly the same loopback hostname, HTTP scheme and port. Production and nonlocal listeners reject those loopback Origin claims, including spoofed Host headers. The owner/MFA session, optimistic revision checks and atomic audit protections are unchanged. This is an origin-boundary defense-in-depth change, not an admin deployment or proof of full CSRF protection.

## Audited emergency application-key revocation (opt-in)
When the existing independent administrative audit table and owner MFA gate are enabled,
the authenticated tenant list offers **Revoke keys** only for a nondemo tenant
with a known positive count of issued application API keys. The operator must
type the exact tenant ID, enter a 10–240 character non-sensitive reason and
confirm that **all** application keys will be invalidated immediately.
The owner-only `POST /v1/admin/tenants/{tenant_id}/revoke-keys` executes an
optimistic, revision-conditional DynamoDB transaction that clears only
`key_hashes` in the authoritative tenant record and inserts a keyed,
tamper-evident, immutable audit receipt in the separately retained table.
A failed audit write, absent keys, invalid hash metadata or stale revision
cannot commit the revocation. The receipt stores only prior and new key
**counts**, never plaintext keys, hashes, customer emails or Stripe identifiers.
Cognito identities and verified Stripe entitlement are unchanged.
The operation cannot be undone by restoring old keys; customers must issue new ones.
It does not provision AWS resources or perform Stripe mutations.


## Persistent signed sensitive-read receipts (opt-in)

With the existing explicitly provisioned and retained administrator audit table, exact Cognito account inspection, server-verified Stripe subscription inspection, and individual audit-receipt lookup first append a distinct signed **read-attempt** receipt. It includes only the authenticated owner, a fixed resource code, UTC time, and optional validated tenant ID. It never includes an email address, provider username, Stripe customer/subscription binding, inspected event ID, request/response body or API key. The response includes `read_audit_event_id` after success and the existing owner UI can verify the signed read receipt by ID. This receipt attests to a read **attempt**, not downstream provider success; failures after logging leave the attempt recorded. If audit append fails, the sensitive read fails closed with HTTP 503 before contacting the provider. Uses only the current opt-in table and its existing put/get permissions; no new services, capacity allocations, plan upgrades or AWS deployments were made. Existing provisioned write capacity may throttle bursts. When the audit table is disabled in the hosted runtime, protected owner reads fail closed with HTTP 503 (rather than operating without persistent audit); local isolated-router tests may still configure optional auditing. #40 remains open for live verification.


## Signed Cognito directory-page read auditing
If the existing optional `GUARDOATH_ADMIN_AUDIT_TABLE` is configured, every authenticated `GET /v1/admin/users` request writes a signed immutable `cognito.user_directory` read-attempt receipt **before** the Cognito provider call. Only fixed resource type, verified owner, UTC time and system scope are stored; no email filter, account identity or paging token is persisted. Audit failures return HTTP 503 without enumerating users. The API and authenticated owner user list show the receipt ID for verification. Without the audit table, hosted owner directory access returns HTTP 503 instead of enumerating users unaudited. Each audited page consumes a write in the existing DynamoDB table; no new resources or AWS capacity are authorized by this source change.

## Signed owner tenant, usage and published-catalog read receipts (opt-in)

When the existing independent admin audit table is enabled, owner-only tenant directory pagination (DynamoDB-backed or local store), exact tenant detail, monthly recorded-usage lookup, published category/check/rule catalog reads and catalog preview requests now append signed, immutable metadata-only read-attempt receipts **before** the store or registry access. Each successful response contains `read_audit_event_id` for verification in the existing authenticated audit viewer, and the relevant UI displays the receipt. Only a fixed resource code, verified owner subject, UTC time and, for exact tenant detail/usage, the validated tenant ID are retained. Search terms, cursors, catalog proposals, provider bindings, raw requests and credentials are never persisted. Failed audit writes return HTTP 503 before protected data reads. A receipt establishes an attempted read, not a successful provider operation. Missing audit configuration blocks protected hosted admin reads with HTTP 503; enabling the corresponding live audit table and IAM still requires deployment configuration and #40 remains open. These are conditional writes to the **existing** audit table and can consume its already-provisioned capacity; this source change makes no AWS changes or provider purchases. The 500-ms PII deadline and tenant enforcement are unaffected.


## Signed owner overview and capability-read receipts
When the existing independent administrative audit table is configured, the
authenticated `GET /v1/admin/overview` and `GET /v1/admin/features` endpoints
each append a separate immutable, HMAC-signed `admin.overview` or
`admin.features` read-attempt receipt before exposing platform metadata.
The receipt stores only the owner subject, fixed resource, system scope and UTC
time; no client token, raw request, customer identity or billing data. Both
responses return `read_audit_event_id`, and the existing owner page displays
these IDs alongside the loaded console status. Audit-write failures prevent
both reads (HTTP 503). When no audit table is configured, these hosted metadata reads return HTTP
503 and do not reveal owner data; the login shell remains accessible. No cost, subscription, entitlement,
tenant policy, rule, PII deadline or infrastructure settings change. This enforces a hosted fail-closed precondition, but does not prove deployed
persistent auditing or live admin acceptance; issues #39–#42 remain open.


## Hosted owner read-audit enforcement (source change, not deployed)

The production environment mount now explicitly requires a signed persisted read receipt for each protected owner data read. A missing `GUARDOATH_ADMIN_AUDIT_TABLE` does not stop the public GuardOath app from mounting: `/admin/login` remains a public sign-in shell, but authenticated owner Overview, Features, Tenant directory, Catalog and all other guarded data reads return HTTP 503 rather than silently accessing unaudited records. Invalid or unavailable audit writes also fail closed before provider reads. Deployment must configure an independent retained DynamoDB audit table and least-privilege PutItem/GetItem permissions before operators can use the data UI; this source change provisions nothing and cannot establish 15 live acceptance or real AWS costs. Directly constructed routers retain explicit optional read auditing for isolated tests; the hosted environment always sets strict enforcement. Each successful data read consumes one signed audit write in already-authorized capacity. No Stripe, tenant, Cognito or shared-engine mutation is introduced.


## Versioned catalog metadata preview fingerprints (review only)

The authenticated **Catalog → Preview description** workflow checks an existing
published category/check/rule against the currently shipped registry version,
then returns two deterministic SHA-256 identifiers: `baseline_sha256` for the
**entire server-projected published registry** and `proposal_sha256` for the
specific next-version candidate, target identities, changed title/use-case
fields and the baseline identifier. A revisioned catalog source change, even
if its version label was mistakenly left unchanged, yields a different
baseline/proposal fingerprint. Dictionary key order does not change either
digest. The owner UI verifies the two lower-case 64-character digests and
labels them as **review identifiers only, not signatures or approvals**.

No draft is durably saved, no independent reviewer is recorded, no immutable
approval is established, and there is **no publish/rollback endpoint** yet.
Do not treat a checksum or a successful preview as permission to deploy a
changed registry. An eventual publisher must independently reload/compare
current source and revision, authorize a distinct reviewer, run positive,
adversarial and original release gates, write a durable approval/audit record,
retain rollback versions, and verify customer tenant Off settings and host
reference trust remain unchanged. Existing read-attempt auditing still protects
the preview route in hosted environments. Neither fingerprint is a MAC or a
proof of provenance.


## Audit viewer resource parity and full-receipt integrity

The owner-only audit receipt viewer recognizes the exact set of protected read
resource codes supported by the signed server audit reader, including
\`admin.overview\` and \`admin.features\`. Previously, receipts generated for
those two legitimate endpoints were displayed on the page but rejected when
operators attempted to verify them by ID. The parity regression now compares
the browser's accepted resource set with the server's authoritative read
resource set, preventing silent drift as future read types are added.

Full signed audit rows containing unsigned, unrecognized attributes are not
treated as cryptographically verified. Existing signed receipt fields, HMAC
algorithm and projection-based reader behavior are unchanged. For direct
DynamoDB reads, the reader projects the approved signed fields; any unrelated
unprojected provider attributes are outside that projection and cannot be
evaluated by the reader. Neither change authorizes new provider requests,
modifies a tenant, relaxes PII validation or represents deployment/live acceptance.


## Owner-only immutable catalog description drafts (not publication)
A verified owner with recent Cognito MFA can preview a next-version rule description and explicitly **Save immutable draft**. The same opt-in, dedicated audit-table configuration used for administrative actions must exist. The server revalidates the entire current published registry before persisting a draft and never treats a browser-provided fingerprint, description, or proposed version as approval. A signed, owner-scoped immutable record captures the published baseline and proposed SHA-256 fingerprints, category/check/rule identity, candidate next version, proposed title/use case, UTC time and audit reason. Conditional DynamoDB PutItem plus an HMAC-keyed idempotency token prevents overwrites; identical replay returns the original receipt, changed intent fails. Signed single-ID read is owner-only, strongly consistent, and detects any changed published source/version as stale. The browser keeps no private Cognito token outside in-memory state, clears private draft input on logout, and warns that a draft is NOT reviewed, approved, tested or published. The draft's data is a separate signed record, not a normal tenant-mutation audit receipt; use the dedicated GET endpoint, not the generic audit lookup.

This uses no new AWS resource or Stripe/Cognito provider write, though use of the existing approved audit DynamoDB table has ordinary AWS transaction costs. Never enable that table or change provisioned capacity without cost approval. Drafts do not modify tenant records, categories, detector code, stage Off settings, host-signed trusted references, subscription entitlements or deployment artifacts. Versioned source publishing/review/test/rollback, exact-SHA CI, production MFA availability and 15 live checks remain unfinished.

## Immutable owner-only catalog review receipts (not publishing)

After an owner with verified Cognito MFA saves an immutable catalog description draft, **Inspect draft** compares its signed baseline and proposal fingerprint against the entire current published registry. Only a nonstale draft exposes **Record signed review**. The operator must type `REVIEW ` followed by the draft ID's last eight lowercase hexadecimal characters, enter a 10–240-character reason without secrets or customer data, and confirm the no-publication warning. A same-origin, owner-authorized POST re-reads the signed draft, recomputes both fingerprints server-side, and persists a separate immutable `catalog.review` HMAC-signed receipt in the existing independent audit table. GET `/v1/admin/catalog/reviews/{review_id}` re-reads and verifies that receipt. The browser verifies the returned receipt identity, source/proposal digests, owner-scoped persistence, and `published=false` before calling the review complete. The authenticated session and read-attempt audit gate still apply. A stale source, wrong owner, malformed confirmation, conflicting idempotency key, failed audit write or signature mismatch is denied; none permits publication. Consistent reads and idempotent conditional inserts prevent overwriting a previous review.

A receipt records that an operator reviewed a specific immutable proposed metadata change. **It is not an independent reviewer approval, a successful validation test, a source commit, a publish authorization, a rule activation or rollback capability.** A later publisher must enforce reviewer independence, exact version/source parity, benign/adversarial tests, signed change history, immutable rollback source and production release acceptance. The current pipeline still cannot modify any shipped category, check, detector, tenant Off flag, Stripe entitlement, host-signed reference or attachment policy. The feature requires an already-authorized administrative audit table; each review/inspect uses its existing DynamoDB capacity; no table provisioning or new AWS spend is authorized by this source change. Exact-SHA CI and 15 live checks remain mandatory before declaring production acceptance. Issues #39–#42 stay open.

### Signed catalog review source parity

Each owner-only signed review inspection now rechecks the signed underlying draft against the complete current published registry. Response fields `stale` and `source_current` distinguish historical HMAC integrity from current source evidence. Same-version changes and version bumps mark old reviews stale; missing or tampered drafts and provider outages fail closed with 503. The authenticated browser verifies the fields and warns on stale historical reviews. Neither a current nor historical review publishes rules, grants entitlements or replaces versioned release tests, rollback and 15 live acceptance.

## Audited Cognito user sign-in access control (source integration, not live release)

The existing authenticated owner-only **Cognito users** panel now exposes separate **Disable sign-in** and **Enable sign-in** actions, requiring the exact account email, a fresh matching Cognito enabled-state inspection, an exact typed `DISABLE <email>` or `ENABLE <email>` confirmation, a 10–240-character non-sensitive reason and a unique idempotency key. The browser never persists the email or token in storage, and each write is protected by a recent independently verified Cognito MFA owner session and same-origin enforcement.

The server performs a bounded exact-email Cognito lookup, a fresh AdminGetUser identity/state check and bounded AdminListGroupsForUser membership verification before every mutation. Both the pre-write and post-write AdminGetUser snapshots must independently contain exactly one matching email and exactly the same stable Cognito subject; case-only email differences are allowed. A changed, duplicated or missing email makes a pre-write attempt unavailable without any Cognito write, or leaves a post-write attempt indeterminate with a signed intent but no outcome. This prevents an old email confirmation from being mistaken for consent to modify an account that changed during execution. It rejects the signed-in owner account, any member of `GuardOathOwners`, ambiguous or changed users, and unsupported account states. The **independent, already-configured DynamoDB audit table** receives an immutable signed *intent* before the Cognito AdminDisableUser/AdminEnableUser call. Only a separate fresh Cognito AdminGetUser verification and a second signed immutable *outcome* receipt allow the API to return `status=verified`. The owner UI verifies the persisted outcome through the audited receipt endpoint. Stored receipts contain a keyed pseudonymous subject fingerprint, booleans, actor, reason, timestamp, correlation ID and HMAC integrity, **not** full email, Cognito username, tokens or raw attributes.

Cognito mutations and DynamoDB audit records are separate provider operations, not one atomic transaction. Failed post-write verification or outcome persistence produces an *indeterminate 503*, never a claim of success. The attempt receipt remains durable and a consumed idempotency key prevents duplicate Cognito writes; **inspect actual provider state and audit evidence before any new action**. Turning on Cognito access does **not** change a tenant's disabled state, MFA requirements, API keys, paid subscription or Stripe entitlement. The least-privilege AdminDisableUser/AdminEnableUser IAM operations are scoped to the existing user pool and conditional on the existing `AdminMutationsEnabled` CloudFormation condition, not merely a public login page.

The feature is source-integrated only. Actual permissions and operations remain subject to approved infrastructure configuration, exact-SHA CI/browser acceptance, 15 production checks and prior cost authorization. No deployment or provider operation was performed as part of its implementation. Operator-controlled suspension is **not** a substitute for the separate customer tenant status action. Billing mutations, cost reporting and versioned platform catalog publishing/rollback remain outstanding under #39–#42.

### Sign-in mutation idempotency and provider-call bounds

Before any Cognito account inspection or mutation, a strongly consistent read of the existing signed audit intent key rejects previously consumed idempotency keys. The conditional intent insertion remains the atomic concurrent-request arbiter if two requests race. A failed audit lookup blocks all Cognito calls (503); an existing key returns 409 without another Cognito provider invocation. After a 503 from a post-provider failure, inspect the account and saved attempt/outcome receipts rather than automatically retrying the same action with a different key. This preserves the fail-closed audit trail and does not alter subscription entitlements or create new provider resources.


### Verified negative billing reconciliation (owner-only; not a paid unlock)
Authenticated \`/admin\` offers **Reconcile unpaid** on saved-active Stripe tenants
when the existing independent signed audit table and a configured Stripe read
credential are available. A typed \`RECONCILE <tenant-id>\` confirmation, audit
reason, idempotency key, and exact tenant revision are mandatory. The backend
independently re-fetches the saved Stripe binding and verifies configured price
and environment. It only accepts non-active states, never upgrades or writes to
Stripe. The conditional DynamoDB transaction compares **both** the exact saved tenant
record bytes and the numeric revision. An out-of-band same-revision edit during
the verified provider read aborts without overwriting the customer's changes,
even if the legacy writer failed to increment the revision. It sets saved
subscription state to past_due/canceled/inactive, sets monthly allowance to **zero**, blocks overages,
and writes an immutable HMAC-signed outcome receipt atomically. A separately
signed read attempt precedes the provider call; the owner UI verifies the
receipt through the authenticated audit API. Provider, revision, audit or
integrity failure returns no success; operator should inspect before retry.
The receipt excludes provider IDs, credentials, payment details and raw bodies.
Tenant rules/Off settings, Cognito, keys and PII deadline remain unchanged.

Paid activation, refunds/credits, invoices, full costs, source catalog
publishing and rollback remain outstanding. This source change is not a live
Stripe operation or a deployment; exact-commit CI and 15 live acceptance remain
mandatory.


## Audited owner invoice history (source only)
- Authenticated owner/MFA `GET /v1/admin/subscriptions/{tenant_id}/invoices` persists a signed protected read attempt before any tenant or provider read. It independently verifies the saved Stripe subscription/customer, configured price and environment before reading at most five provider invoices per page from server-owned IDs. The existing Billing view supports bounded status filtering and encrypted continuation as described below.
- Every invoice must match the same customer, subscription and livemode with valid status, UTC timestamp and nonnegative minor-unit amounts; malformed or ambiguous pages fail closed. UI excludes provider IDs, customer email, links and payment method. Zero invoices in a verified page is distinct from unavailable provider data.
- Read-only historical sample, not paid entitlement, revenue, a future invoice forecast or refund capability. No Stripe mutation, new service, AWS deployment, 500-ms privacy deadline change or 15 live acceptance is claimed. Exact-SHA CI and live provider acceptance remain required.

### Provider collection-attempt evidence (shown in owner Billing)
For each **verified** invoice the bounded provider page now projects only collection method
(automatic or send-invoice), whether an attempt is recorded, bounded attempt count and
a provider-recorded next-attempt timestamp in UTC. All four fields are **unavailable**
when Stripe does not provide them; missing evidence is never treated as zero attempts.
The authenticated Billing UI displays this data on open invoices with a positive
remaining balance and labels it **not proof of payment failure**. These fields are
provider-reported hints, not paid entitlement, overdue status, charge success,
a forecast, a payment failure, or authorization for a refund. Malformed or ambiguous
provider types fail closed without rendering a partial financial page. No additional
Stripe API call or provider mutation is introduced.

## Read-only administrator infrastructure inventory
From the repository root, with AWS SSO credentials available locally, run:

```powershell
python -m tools.verify_admin_activation --profile collectiverse-prod
```

The tool reads the existing stack parameters/outputs, Lambda feature flags, and Cognito MFA/group configuration without changing any AWS resource. It prints only predetermined booleans, never account identifiers, client IDs, user data or credentials. Exit code 2 means **not authorized for activation**; the tool always reports `signed_current_login_mfa_verified=false` because static configuration cannot attest to a real recent MFA login. Even when the admin infrastructure is provisioned, this is an inventory, not proof of authenticated admin access or a substitute for 15 live checks.

For a legacy stack that lacks `EnableAdminConsole`, treat a missing parameter as unavailable rather than assuming the admin application is broken. Preserve `EnableSelfRegistration=1` in any future CloudFormation preview, and verify all unrelated modifications and conditional replacements before executing it. Classic hosted UI claims remain insufficient; use the separately enabled native flow only after actual pool evidence passes the unchanged verifier. Do not fake `amr`, treat user MFA enrollment as recent authentication, or upgrade to a potentially billable tier.

## Optional Cognito Lite API software-token administrator sign-in

The separate admin public Cognito app client can opt into a bounded two-step
API sign-in without upgrading the user pool from Lite or switching the
existing customer hosted UI: set `EnableAdminNativeMfa=1` **only** in a
reviewed CloudFormation update with `EnableAdminConsole=1` and an independent
owner signing key. Both options are default-off. The stack permits
`ALLOW_USER_PASSWORD_AUTH` exclusively for the admin app client when opted in,
not for the existing customer app client.

The GuardOath administrator login page then offers password followed by a
six-digit authenticator-app code. The server calls Cognito `InitiateAuth`
and **requires** `SOFTWARE_TOKEN_MFA`; password-only sign-in, setup-required,
unrecognized challenges, malformed signatures, stale challenge tickets and
provider failures do not release administrator tokens. A 3-minute HMAC-bound
challenge ticket remains in transient browser memory (never local storage);
it contains a Cognito challenge session and must be treated as sensitive.
The password and OTP are cleared immediately in the browser and never
stored in a tenant or audit record. Same-origin, limited Cognito calls and
content-free errors apply. This login does not enroll a factor. An approved
owner without an authenticator can use the separate first-time workflow
below when independent audit storage is configured.

On successful Cognito TOTP completion, the original live admin authorization
still independently requires a **signed** access token whose `amr` includes
`mfa`, a recent signed `auth_time`, live enabled Cognito account and
`GuardOathOwners` group. No signed AMR or no authenticator challenge means
**no admin session**, even if credentials and TOTP otherwise succeeded.
Cognito's ACR/AMR rollout may not have reached a specific pool; verify
a real current-login token, not the classic hosted UI's default claims,
before production activation. OAuth/PKCE remains an independent existing
sign-in path; it is not evidence that classic hosted UI achieved MFA.

Activation is not automatic: the current deployed CloudFormation stack and
owner enrollment must be reviewed, registration must remain enabled, no
existing Cognito pool/customer Lambda/CloudFront resource may unexpectedly
be replaced, no paid tier change is authorized, and all exact-source CI and
15 live post-deployment checks remain required. The new source alone does
not enable `/admin` or provision an owner account.

## First authenticator enrollment for an existing owner

The actual `/admin/login` page offers **Set up first authenticator** only when
`GUARDOATH_ADMIN_ENABLED=1`, `GUARDOATH_ADMIN_NATIVE_MFA_ENABLED=1`, and a separate
`GUARDOATH_ADMIN_AUDIT_TABLE` are configured, and the operator has explicitly set
`GUARDOATH_ADMIN_ENROLLMENT_SUBJECT` to the one intended owner's immutable Cognito
`sub`. The corresponding CloudFormation parameter `AdminEnrollmentSubject`
defaults to an empty value, is NoEcho, and accepts one canonical UUID only.
Never use an email, wildcard, group name or comma-separated list. The verified
signed subject must exactly match this designation before live-owner checks,
state reservation or association. The designation grants neither owner membership
nor an administrator session and is not exposed by public configuration.
There is no extra resource or
automatic activation. An unavailable control store fails closed. With no audit
table, already-enrolled owners can still attempt existing native sign-in, but
enrollment is absent and protected owner data reads remain unavailable.
An unset/empty designation also removes the enrollment routes and displays setup
unavailable guidance while preserving existing native MFA sign-in. A malformed
designation fails enabled-admin startup before routes mount.

The account must already be enabled, `CONFIRMED`, have matching verified email
attributes, and belong to the live `GuardOathOwners` group. Customer sessions,
API keys, subscriptions and token group claims do not grant this capability.
The server performs fresh `USER_PASSWORD_AUTH` against the independent admin
client itself, then verifies the real RS256 access token's pool, client, scope,
expiry and signed `auth_time`. The browser cannot supply its own access token.
`MFA_SETUP`, existing TOTP challenges, reset-required accounts and other
authentication results are refused by this specific bootstrap workflow. It
requires a pool that permits a not-yet-enrolled owner to receive a password
authentication result; it does not change pool MFA configuration to obtain one.

Both `GetUser` and `AdminGetUser` must agree on subject, username, verified email
and absence of active MFA/preference/legacy SMS settings. Malformed or duplicate
attributes, revoked tokens, disabled accounts and incomplete group pages deny.
The live identity is rechecked before association, verification, preference
activation and final confirmation. Cognito's MFA lists report **activated**
factors; they do not prove that a legacy account never registered a subsequently
deactivated factor. The exact-subject designation is an explicit operator
authorization, not proof of that historical absence. Only designate an established
first-time owner. Historical/reset cases require separately authorized operator recovery;
do not infer first-ever enrollment from an empty MFA list alone. See
[GetUser](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_GetUser.html)
and [SetUserMFAPreference](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_SetUserMFAPreference.html).

### What the owner sees

Enter the account email and password, then choose **Set up first authenticator**.
The password is immediately cleared. The returned key stays in a read-only,
password-masked field; **Copy setup key** copies it only on an explicit click.
Add a time-based code for GuardOath in an authenticator app, then enter its
six-digit code and choose **Save authenticator**. No external QR service,
analytics call or third-party script receives the key. Clear the clipboard after
pasting; this page cannot promise to erase a system clipboard or clipboard history.

The setup key, code and sealed ticket are transient. Completion, cancellation,
expiry, sign-out and page exit clear them from the page. A late response cannot
restore a cancelled setup. Nothing is placed in localStorage/sessionStorage.
A confirmed code mismatch allows a retry of the same pending flow, up to five
provider verification attempts within the original three-minute deadline. Any
other uncertain result clears the local setup and advises operator investigation.

Successful setup returns only `completed` and `sign_in_required`, with no token
or admin session. The owner must enter the password again and complete the
existing native TOTP sign-in. The unchanged verifier still requires signed
`amr` containing `mfa`, recent `auth_time`, enabled identity and live owner
membership. A password-only token remains unusable for every admin data route.

### Encrypted ticket and durable control state

The three-minute AES-GCM ticket binds purpose, pool, client, subject, username,
verified email, original signed authentication time, access token and random
control nonce. A random 96-bit GCM nonce and purpose-specific key derivation
separate it from existing cursor/audit/challenge keys. It contains no password,
OTP or setup key. The access token is encrypted and never returned in plaintext.
`AssociateSoftwareToken(AccessToken=...)` can return only `SecretCode`; a provider
Session is neither required nor invented. `VerifySoftwareToken` uses the same
sealed access token and the user's code. AWS documents these separate token and
challenge authorization paths in
[AssociateSoftwareToken](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_AssociateSoftwareToken.html)
and [VerifySoftwareToken](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_VerifySoftwareToken.html).

Control keys begin `enrollment-control:` and then a context-bound HMAC. They
cannot be accepted as the 64-hex IDs of genuine audit receipts. Exact signed
control documents contain only version/kind, subject, nonce, issue/expiry times,
state/phase, attempt count and revision. No email, password, code, bearer token,
setup key or provider session is stored in DynamoDB. Strong reads and conditional
full-document replacement protect the complete prior nonce and signed state.

| State/phase | Authorized next action | Expiry or cancellation |
| --- | --- | --- |
| `PENDING / ASSOCIATING` | Claimed before association; change to READY only after a valid provider reply | Never automatically reclaimed or cancelled |
| `PENDING / READY` | Matching sealed ticket may claim verification | May cancel, or be replaced after expiry by a fresh password-authenticated eligible owner |
| `VERIFYING / READY` | One winning instance may verify, independently recheck identity, activate preference and prove the final live state | Never automatically reclaimed or cancelled |
| `COMPLETE / READY` | Terminal first-setup record | No reset/replacement through enrollment |
| `BLOCKED` | Operator investigation | No automatic retry |
| `CANCELLED / READY` | Fresh password authentication may claim a new nonce | Old tickets always fail CAS |

Only a definitive Cognito `CodeMismatchException` may return VERIFYING to READY,
keeping the same nonce and expiry. Unknown provider or persistence outcomes never
trigger automatic write retries or report success. Production uses a separate
bounded Cognito SDK client with `total_max_attempts=1`; existing login/read SDK
behavior is unchanged. The service permits two concurrent requests and twelve
requests per minute per warm instance; these are not fleet-wide limits. Cognito
quotas and any existing edge controls remain relevant. A persisted five-attempt
cap applies across instances to each pending nonce.

### Capacity, recovery and rollback

A normal successful setup performs four control writes and three strong control
reads in the already-approved independent audit table, plus the bounded Cognito
calls. Existing receipt traffic shares this capacity. Do not add a table, raise
capacity or rely on member-account free-tier arithmetic without the actual payer
allowance/budget evidence. This change adds no AWS resource, tier, concurrency,
memory or timeout setting. Audit-table enablement still requires the production
cost and release gates.

The existing Lambda timeout remains unchanged. A timeout or lost response after
association/verification can leave a blocked control record even when a provider
operation succeeded. Preserve that record and ordinary audit receipts. First
try a fresh existing password+TOTP login if the owner saved the authenticator.
An authorized operator must independently check the exact identity, live factor
state and signed control state before deciding any recovery. There is no public
reset/delete/unlock endpoint. Do not delete a control row, rotate its key/client
namespace, disable a factor, or launch a new enrollment to bypass an ambiguous
ASSOCIATING/VERIFYING outcome. Do not run external factor mutations concurrently
with this workflow; Cognito does not expose an atomic factor-state precondition.

Immediately after successful setup **and a fresh verified MFA sign-in**, clear
`AdminEnrollmentSubject`/`GUARDOATH_ADMIN_ENROLLMENT_SUBJECT`. Native MFA login
stays enabled; the setup capability and its public endpoints disappear. Also
clear the designation before any audit-HMAC key rotation or recovery work.
Preserve all existing terminal/ambiguous control records. Re-enabling a subject
does not override COMPLETE or unknown-outcome state in its current namespace.

Rollback restores the previously verified application ZIP and its environment
snapshot, preserving Stripe values and all existing resource identities. It
must preserve audit/control records and any authenticator already registered.
Clearing the enrollment designation disables setup independently. Turning off
the native MFA flag also disables native sign-in. A rollback never removes owner membership,
disables an authenticator or rewrites the customer's pool. Review and run the
exact-source CI and the existing 15 live checks before claiming activation.

`cryptography==50.0.2` is pinned in the AWS and test extras. Its CPython ABI3
manylinux2014 wheel and cffi/pycparser dependencies resolve under the unchanged
CPython 3.13 builder; CPython 3.11, 3.14 and 3.15 binary resolutions also pass,
and the local suite runs on 3.12. The resolved 3.13 wheel set totals 5,004,265
compressed bytes. This is dependency-resolution evidence, not the final Lambda
ZIP size: the exact committed production build must still satisfy the
52,428,800-byte upload limit. See the [official release](https://pypi.org/project/cryptography/50.0.2/).

Both CloudFormation app clients explicitly retain `WriteAttributes: [email]`
and read access to `email_verified`. AWS forbids granting app-client write
access to verification attributes; omitting the write list would broaden
defaults. The read-only Google registration preflight now checks the same narrow
write list and readable verified-email assertions. These source corrections
change no live/customer client and do not enable Google sign-in.


## Owner workspace navigation and visible results

The owner interface now has seven sections: Overview, Users, Tenants, Billing,
Usage, Audit and Catalog. Desktop navigation remains in a sidebar; small screens
use a compact wrapped navigation grid. The active section is named in the page
title and identified for assistive technology. Every existing administrative
control and its server authorization remains in place.

From **Tenants**, choose **Usage**, **Verify Stripe** or **Invoices** to open the
matching result section immediately. Loading and provider-failure messages appear
with the selected results. A native MFA login followed by a protected-data failure
now shows the error in the visible workspace instead of the hidden sign-in form.
Unavailable platform totals remain unavailable; navigation does not create new
metrics, grants, provider writes or source access.

For an isolated visual review of the actual source interface, run:

```sh
node tools/render_admin_preview.js /tmp/guardoath-admin-preview.html
```

Open the resulting HTML file in a browser. Its permanent preview banner explains
that accounts, invoices and audit receipts are synthetic. All HTTP responses and
OAuth callback state are confined to a lexical test adapter in this generated
file; it cannot connect to Cognito, Stripe, AWS, or production tenant APIs. The
production HTML and JavaScript are extracted from `admin_browser.py` unchanged.
This review copy is not authentication evidence or an AWS deployment.

`tests/browser/admin_console.spec.js` executes the real source with synthetic API
fixtures: seven-section navigation, mobile overflow, result navigation, visible
post-MFA failure, directory paging/rate failure, 401/403 clearing, safe text and
logout. These tests supplement server tests; live MFA, provider checks, required
exact-SHA release jobs and the 15 production checks remain separate requirements.


## Invoice page payment-review signals (2026-11-20)
An independently verified Stripe invoice page includes `payment_review` with counts of **open invoices with remaining balances** and **uncollectible invoices**, plus a currency-separated sum of remaining open balances. The owner UI cross-checks the summary against redacted invoice rows and fails closed on mismatched counts or amounts. A page is limited to five invoices; `complete_history=false` applies when more results exist and to every filtered or continuation page, including the final page. These are bounded operator-review signals **not** verified payment failures, aging, account-wide receivables, collected revenue, refunds, entitlement status or AWS costs. Reading another page requires an explicit owner action; no financial write or tenant-policy change occurs.


## On-demand recorded-request quota pacing (source integration)
The authenticated **Tenants → Usage** view now separates the stored monthly
quota threshold from advisory linear month-end recorded-request pacing.
Projection uses the authoritative current-month counter, UTC day, saved
allowance and an updated-at record timestamp within 48 hours. It is
suppressed for missing counters, invalid/future/stale timestamps, zero
allowance and fewer than seven elapsed calendar days. A saved zero is
not a missing counter. The server distinguishes projected risks from
already-at/over-cap observations, and the UI shows the unchanged stored
enforcement setting. Fresh record metadata does not prove request-ledger
completeness; this low-confidence estimate is not a live bill, AWS cost,
revenue forecast, persistent alert or a change to customer settings.
No new provider reads, infrastructure or spending. Exact-SHA CI and
live 15-check acceptance remain separate.


## Reviewed metadata manifest (source-only, not publication)

A current, HMAC-verified catalog draft and its HMAC-verified owner review can be used
to calculate a deterministic metadata-only **release manifest** through the
authenticated, signed-read-audited
`GET /v1/admin/catalog/reviews/{review_id}/manifest` endpoint. The existing
Catalog workspace enables **Preview reviewed metadata manifest** only after its
independent signed review read confirms the source is current. The server fetches
the published registry once, checks the whole-source and proposal hashes,
applies only title/use-case metadata to an in-memory projection for the exactly
next registry version, and returns a candidate fingerprint, exact target/field
diff and baseline fingerprint for a future rollback. A stale registry responds
409; missing/tampered review or audit failure responds 503. Neither operation
writes a catalog, modifies a tenant or changes a detector.

The manifest is **not** an executable registry, source publication, rollback
action, rule approval, evidence of acceptance tests, or authority to deploy.
Source generation, versioned release tests (benign/adversarial/negative/timeout),
exact-SHA CI, rollback implementation and 15 live checks remain outstanding.
No new AWS capacity, provider charges or privacy deadline change is introduced.


## Recovering indeterminate Cognito owner actions

The owner Users workspace now displays the one-time action-reference key used for a confirmed account disable/enable operation. If the independent provider recheck or signed outcome write fails, **do not replay the action**: enter the same reference in **Inspect signed action**. The MFA-authorized, same-origin, signed read-before-provider operation `POST /v1/admin/users/status/receipt` checks strongly consistent persisted signed intent/outcome rows. `not_found` means no signed intent for the owner/reference; `indeterminate` means an intent exists but the Cognito outcome is not verified; `outcome_recorded` verifies the historical signed pair. None establishes the **current** provider state; independently inspect the exact Cognito account before additional changes. The endpoint performs no Cognito call or mutation, exposes no email, refuses tampering and audit outages, and never grants paid entitlement. The transient reference is cleared on sign-out. Source changes are not evidence of production deployment.

## Catalog evidence visibility (source only, 2026-11-21)
The guarded owner Catalog inventory lists published category/check/rule metadata, eligible Input/Output stages, supported actions, check use cases and limitations, plus counts of unit-test and evaluation files **declared** by the published registry. That original inventory API remains counts-only and does not return filenames, detector paths, positive-example content or test fixtures. The separate exact-target inspector described above now makes bounded published source declarations visible without changing this projection or its signed-history fingerprints. Counts and paths never attest that current-SHA CI ran, that acceptance passed, or that the system was deployed; missing declarations are unavailable, not zero. Catalog reads reject malformed or duplicate IDs, incomplete rule objects, inconsistent declared rule counts, and ambiguous stages/actions before returning partial results, including in filtered views. This inventory does not publish, roll back, or modify system rules or tenant-saved Off policies. Versioned publishing, live AWS cost reporting and 15/15 acceptance remain open.

### Ambiguous published IDs and preview safety
A duplicate published category, check or rule ID fails the inventory read and is an explicit stale/ambiguous-source conflict (HTTP 409) in the reviewed metadata-preview workflow; the proposal is never saved or published. Other malformed source objects and provider failures remain 503 unavailable. These are separate, fail-closed outcomes; neither authorizes changes.


### Signed Cognito account-action recovery and optional live comparison

The owner **Users → Recover an account-action receipt** control accepts the
original action reference alone for owner-bound signed historical intent/outcome,
or the reference plus **exact account email** to compare against a fresh Cognito
\`Enabled\` read. Only an MFA-verified owner may call the same-origin
\`POST /v1/admin/users/status/receipt\`. Exact email stays in the POST body,
never an access URL, saved action receipt or returned account record. The signed
protected-read attempt must persist *before* either Cognito lookup, and the
existing per-owner rate budget applies.

An independent provider lookup must match the saved signed action's HMAC
subject fingerprint, require a unique exact-email identity and agree with a
second Cognito account read. A different subject, ambiguous user, malformed
identity, signed audit tampering, owner mismatch, unauthorized caller or audit
outage prevents verified comparison. The API never retries the original
suspension or reactivation, changes entitlements or exposes raw account IDs.

A current Enabled snapshot is **not** a provider mutation receipt. If the
saved intent has no signed outcome, status remains **indeterminate** regardless
of current Enabled state; if it has an outcome, the browser explicitly reports
whether the current state agrees with that historical snapshot. Both action
reference and optional email are cleared on sign-out. This code is source
progress only until exact-SHA CI, production MFA and all 15 live checks pass.

## Advisory historical recorded-request volume (source only; 2026-11-21)

The owner **Tenants → Usage** view compares the existing gated month-end request-count projection with the median of **three consecutive preceding UTC months** in the authoritative tenant usage record. Absent months remain unavailable (not zero). Early observations, stale record metadata or a missing current-month counter suppress the comparison; a prior-month median under 30 suppresses misleading percentage differences. Possible surge (200%+), elevated (150%+), lower (50% or less) and typical-range descriptions are purely advisory.

This is neither a verified AWS cost anomaly nor a Stripe invoice, automated alert or quota enforcement. Historical counters can be incomplete. A visible browser test and API tests cover owner access, missing history, year boundary and non-mutation. No production AWS change, outside provider request, additional spend, subscription change or PII deadline change is implied.


## Fail-closed published-catalog metadata admission (2026-11-21)
The owner Catalog API validates the **entire** versioned source registry before
projecting any category, including when a category filter is supplied. Published
identifiers, text fields, stage/action eligibility, evidence declarations and
bounded domain/check/rule inventories must be well formed. Explicitly malformed
empty eligibility values are not normalized into valid empty lists, and invalid
metadata objects are never rendered as arbitrary strings. A malformed provider
snapshot yields an unavailable response rather than a misleading catalog or
leaking internal source contents. Returned eligibility lists are copies, not
mutable references to provider data. This is an admission safeguard for future
versioned catalog publishing, **not** a publisher or a claim of live deployment,
current-source CI, or a modification of tenant settings and the shared Engine.


## Exact-record transaction concurrency guard (2026-11-21)

All four owner-initiated tenant mutations (account status, stage-specific rule action,
emergency allowance cap and application-key revocation) now use one DynamoDB
transaction that conditions the tenant write on **both** its saved revision and the
exact original stored tenant document. This prevents a concurrent legacy writer
that accidentally changes the document without increasing its revision from being
silently overwritten. A failed compare-and-swap also prevents the paired signed
audit receipt from being recorded and returns an unavailable action rather than
a false confirmation. Customer tenant policies, Stripe bindings, subscription
entitlement, input/output saved Off settings, host reference trust and fail-closed
attachment behavior are not modified by this change. This protects source state;
it is not a substitute for revisioned provider reconciliation or deployment tests.


## Exact saved identity and stage-map guards for owner tenant actions (2026-11-21)

All four owner-initiated tenant writes (status, rule action, downward allowance cap and API-key revocation) require the **stored** `demo` field to be the literal JSON boolean `false`. A missing value, null, numeric zero/one or text that resembles a boolean is corrupt or ambiguous and is refused before an atomic write. This does not change a customer's access, subscription status or valid tenant data. In the stage-specific rule editor, an absent rule-action map retains the existing legacy empty default, but an explicitly present null, false, list or string is rejected rather than silently normalized. Both protections preserve the authoritative saved Input/Output Off settings; no DynamoDB tenant update or signed audit receipt is written for these rejected records. Recovery requires an independently verified tenant-record correction, not an admin override.


## Owner tenant-directory source integrity (2026-11-21)

The authenticated Users/Tenants console presents a bounded DynamoDB directory page, not a verified global account count. Each page is validated server-side before any tenant is shown. A persisted missing, non-boolean, or corrupted `demo` identity cannot appear as a normal customer; invalid account or subscription states, duplicate tenant IDs, malformed revisions, or invalid DynamoDB usage/checkpoint structures cause an unavailable (503) page rather than a partially trusted result. The error response omits raw account and provider data. Bad query syntax and invalid owner-bound cursors remain request errors (422), not source outages.

Legacy records with no provider-verified subscription status remain **unverified (null)**, never automatically active or paying. Missing legacy key lists and invalid allowance metadata remain unknown, not fabricated zeroes. The dashboard's authenticated tenant controls still require a real saved non-demo tenant and independent server-side authorization, with signed mutation audit and conditional version checks. This change does not query Stripe, charge AWS, alter customer records, loosen any validation rule or update the 500-ms privacy deadline. Complete CI and live production acceptance remain separate requirements.

## Stripe source admission and malformed provider results (2026-11-21)
Owner Billing and Invoices verify the saved customer identity, exact customer/subscription binding, account status, billing mode and subscription state before initiating a provider read. The persisted `demo` identity must be an actual Boolean; missing or malformed flags never silently become a demo or an eligible paid customer. Missing bindings are still reported as unbound only for otherwise valid customer records. Corrupt saved identifiers or statuses fail closed as a sanitized unavailable result, with no Stripe request. A provider status that is an object, array, Boolean or other non-string is similarly unavailable, not a successful subscription or an uncaught error. This adds no Stripe mutation, customer entitlement or paid service. The browser makes a verification failure visible as unavailable; operational reconciliation still requires independent verified provider evidence and signed audit.


### Catalog draft description binding and timestamp deadline hardening (2026-11-21)
An owner catalog draft now persists only the complete title/use-case values that the server's current-registry metadata preview actually returned. Edited or omitted preview values, forged change fields, malformed before/after descriptions and mismatched visible text fail before any audit-table write. Surrounding whitespace is normalized, and identical idempotent replay still returns the existing signed receipt. This is strictly metadata review; no published check/category/rule, tenant Off action, trusted reference, subscription or detector changes. The built-in, local, non-yielding timestamp checker no longer schedules an uninterruptible event-loop timer; it retains the original monotonic completion deadline and fail-closed late verdict handling. Custom asynchronous timestamp checkers retain cancellation. The intermittent Python 3.12 CI timestamp timeout remains a disclosed scheduling risk until independent sustained validation; no deadline was raised or test skipped. Exact source CI and the 15 production acceptance checks remain mandatory.

## Catalog description and review spoofing guard
In **Catalog**, expand the category and check, then select **Preview description**
beside the rule. Edit its title/use case and select **Preview description change**.
Catalog projections and owner-authored metadata drafts/reviews now refuse
invisible Unicode format characters (including bidirectional overrides and
zero-width text), C0/C1 controls, surrogates, and Unicode line/paragraph
separators. The authenticated admin UI shows a visible rejection before
sending invalid descriptions or review reasons. Server-side validation remains
authoritative if the browser is modified or bypassed. Visible Latin, Arabic,
Hebrew and CJK text, combining accents and standalone emoji remain eligible
under the existing length/content rules. Zero-width joiners remain forbidden
format characters; this guard does not detect every form of Unicode spoofing.
This is metadata review integrity, not a detector,
catalog publication, tenant policy change, Stripe operation, or proof of live
production activation.

## Cognito group-list integrity on owner account changes
Before any owner-directed enable/disable operation, the existing signed-audit-backed Cognito workflow now refuses malformed, duplicated, oversized, or paginated group listings, including empty continuation tokens. The server requires a complete and unambiguous current membership list before creating an action intent or issuing a Cognito mutation. This adds no provider calls, does not permit owner-account changes, and never grants subscriptions or customer access. A successful account action still requires an independently verified Cognito outcome and persistent signed intent/outcome receipts. Cognito cannot provide an atomic cross-service account-and-membership compare-and-set; subsequent membership changes remain an operational race and must never be described as eliminated.

## Owner tenant-detail source integrity (non-DynamoDB fallback)
The individual owner tenant-detail API and the in-memory/non-DynamoDB tenant directory now independently validate the saved tenant identity, literal demo Boolean, account status, subscription state, revision and bounded usage map before returning any account. Tenant IDs must agree with the requested or enumerated key. A corrupt, missing or duplicated source record returns sanitized HTTP 503 instead of silently presenting a paying customer, a different tenant, an unverified zero or a raw provider object. Legacy malformed monthly allowances and key metadata remain explicitly unknown (null), and absent subscription verification remains null rather than a fabricated entitlement. This changes only administrative read projections; the authoritative tenant, Stripe reconciliation and customer policy remain unchanged.

## Reversible reviewed catalog metadata source plan (not published)

After an owner records and independently verifies a signed catalog draft and review, the authenticated **Preview reviewed metadata manifest** action returns a `reversible_patch` with the exact category/check/rule ID, registry versions, SHA-256 fingerprints of the original and candidate catalog metadata projections, a changed-field forward plan, and its inverse rollback plan.

The server first builds the reviewed candidate from a single independently verified published registry snapshot. It then reverses the changed metadata on a deep copy and requires complete projection equality and an exact SHA-256 fingerprint match with the original before setting `round_trip_verified: true`. The owner UI checks the forward and reverse versions, fingerprints, target identity and nonpublication flags before displaying that claim; malformed results fail closed.

This is **only a reversible metadata projection plan**, not executable `control_registry.py` or `rule_catalog.py` source, an approved release, a system-catalog publication, a live rollback, or evidence that acceptance tests were run. `executable_source_committed` and `published` remain false. Versioned source generation, benign/adversarial/timeout tests, exact-SHA CI, guarded deployment, rollback acceptance and all 15 live checks still remain for issue #42. No customer tenant settings, per-stage Off flags, host-signed reference trust, attachment hard Blocks, detectors or payment entitlements are changed.

## Sealed owner directory pagination (2026-11-22)

The authenticated Users directory now returns a version-4, short-lived AEAD-encrypted pagination cursor instead of a signed but readable base64 document. The server binds it to the independently verified Cognito owner subject, source pool, search filter, page size and expiry; the upstream Cognito continuation token is not exposed in browser-readable plaintext. The cursor remains an opaque bearer continuation hint, **not** admin authentication, a tenant ID, a permission grant, or a cross-owner share link. Refresh the Users list after a deployment because earlier plaintext cursor versions are deliberately rejected. Unmodified per-owner provider-read budgets, MFA/RBAC, independent read auditing and provider-data minimization remain in effect. Cryptography comes from the existing pinned AWS/test dependency; no provider calls, AWS resources, subscription billing or tenant policy change are introduced by this sealing mechanism.

## Exact tenant-source binding for owner usage and Stripe reads (2026-11-22)

Every owner detail, usage, subscription-verification and invoice-history request
must resolve to the **same exact authoritative saved tenant ID** as the URL.
If a stale index or corrupted store returns a different customer, the API
returns sanitized **503 unavailable** after its existing protected-read audit;
it does not display that customer's usage, call Stripe under the wrong binding,
or assert a payment or subscription state. A missing account remains 404.
The existing owner-only Cognito/MFA authorization, provider verification,
tenant entitlement and audit-before-read behavior are unchanged. This guard
does not grant payments, change tenant state, add provider spend, or establish
that the live site has been updated.

## Live owner membership integrity during first-TOTP enrollment (2026-11-22)

The first-authenticator enrollment endpoint requires the same verified **complete, unique, well-formed Cognito group membership** as the protected owner-admin session: duplicate owner or other-group entries, invalid names, oversized pages and malformed group documents deny eligibility before associating an authenticator. The enrollment path still rejects every presence of a pagination continuation marker (even null), since a partial membership listing cannot establish authorization. Distinct valid additional groups are permitted only alongside a verified `GuardOathOwners` membership. Membership is independently rechecked before associating a TOTP factor and before verifying a submitted code; corrupt replies fail closed without the next Cognito mutation. This uses the existing Cognito reads, no new provider calls or AWS resources, and does not create an admin session or confer paid access.

### Saved-cap signal versus forecast freshness (2026-11-22)

In **Usage and costs**, a saved current-month request count already above the authoritative monthly allowance is reported as **already over limit**, even in the first six calendar days or when record update metadata is stale. An exact saved at-limit count is likewise reported as **already at limit**. These are observations of persisted request counters, not cost calculations or proof the counter is complete. A month-end request projection remains **Unavailable** unless the separate freshness and minimum-observation gates pass. The page continues to show whether the saved overage policy blocks or merely records excess requests; no extra AWS or Stripe read, paid entitlement, hard-cap change, or persisted alert is created.

## Exporting a reviewed catalog source plan (not publication; 2026-11-22)

The owner Catalog screen can now **Export reviewed forward/rollback JSON** only after
the current, signed, owner-bound review is rechecked by the protected API and
its versioned metadata manifest passes UI source/target/fingerprint and
forward/inverse field-attribution checks. The exported JSON contains only
allowlisted rule metadata, draft and review references, source version and
fingerprints, and the reversible before/after description changes. It cannot
contain the Cognito session token, customer records, private audit credentials
or detector implementation paths. A changed draft selection, refresh,
inconsistent manifest or sign-out disables the export and clears its
in-memory copy. The download never calls a provider or changes a tenant.

This JSON is **an unsigned, non-executable source-review artifact**. Its hashes
are fingerprints, not cryptographic signatures or release authorization. It
does not update `rule_catalog.py`, `control_registry.py`, any deployment or
tenant policy; a source implementation, exact-SHA tests, live 15-check
acceptance and a verified rollback are still needed for issue #42.

## Cognito user-status snapshot parity on owner writes (2026-11-22)
For an authenticated owner enabling or disabling an account, the exact-email
`ListUsers` result and independently fetched `AdminGetUser` record must agree
on identity, Boolean enabled state and account status **before** creating the
durable mutation intent or calling the provider write. Even an empty directory
pagination token is an incomplete/ambiguous result and fails closed. After a
Cognito write, the same account status must remain consistent alongside its
independently verified identity and changed enabled state; unexpected status
drift leaves the signed intent as **indeterminate** instead of recording false
success. Never replay that idempotency key automatically; inspect the owner
action receipt and current Cognito status. This avoids an observed two-read
status race without claiming an atomic Cognito compare-and-set. The existing
MFA, group, audit-first, tenant, entitlement and provider-spend boundaries
remain unchanged.


## Overview complete-page account and tenant totals (2026-11-22)

Owner Overview reuses its existing protected Cognito Users and DynamoDB Tenants
directory reads to display an observed count only when a **complete bounded
first page** can be proven: `status: ok`, explicit `next_cursor: null`,
matching bounded integer `returned` and list length, plus equal
`scanned` and `returned` for the unfiltered DynamoDB page. A zero is
shown only on a complete observed empty directory; malformed, paginated,
failed or unverified reads show **Unavailable** and revoke stale previous
counts on refresh. DynamoDB tenant-record counts include demos and are
not equivalent to active subscriptions, paying customers or a transactional
snapshot. Cognito first-page observations are not a guarantee against
concurrent account changes.

No new provider reads, subscriptions, credits, costs, revenue calculations,
AWS Cost Explorer calls or tenant writes are introduced. `/v1/admin/overview`
keeps unsourced global financial totals unavailable. Counts are calculated
from the independently owner/MFA-authorized `/v1/admin/users` and
`/v1/admin/tenants` responses already in the same UI refresh. Exact-SHA
browser acceptance and live administrator verification remain required.

## Versioned source catalog publication preparation (2026-11-22)

The owner Catalog export is still a **non-publishing review document**. A distinct
offline source-release operation now validates it against the complete current
shipped catalog, independently recomputes the baseline, proposal, candidate and
inverse fingerprints, and rejects stale versions, unknown targets, extra fields,
tampered before/after values, changed detector metadata and unsafe descriptions.
No browser token, HMAC key or customer record is included in the source file.

From an approved checkout, after independently reviewing the export and source
diff, run:

```sh
python -m guardoath.catalog_source_release --plan /secure/path/reviewed-plan.json
python -m guardoath.catalog_source_release --plan /secure/path/reviewed-plan.json --write --confirm-registry-version 4
```

Replace `4` with the *new* reviewed version. The second command only writes
`guardoath/catalog_metadata_releases.json` in the local checkout, atomically.
The tracked release history is source-controlled, append-only and metadata-only.
When code is built from a reviewed new version, its `registry_payload()`,
admin Catalog and customer rule descriptions display the shipped title/use case
and monotonically incremented registry version; all rule IDs, examples, detector
implementations, saved tenant actions and entitlements stay unchanged.
The previous release record and field before-values provide a reproducible
inverse, but a rollback must itself be separately reviewed and shipped as a
**higher** version, not a silent production version decrease.

This does NOT commit, merge, deploy, publish to live AWS, run remote acceptance,
write an admin-audit receipt or independently establish that the owner review was
authorized. An exported SHA-256 is not a signature: source review and the exact
SHA CI/CodeQL/Playwright, provenance, guarded deployment, rollback and 15 live
acceptance checks are mandatory. Source release records alone never grant
publishing permission through the API. The protected admin Catalog API remains
read-only with respect to published definitions.


## Shipped catalog source history in owner Catalog
The existing owner-only, signed-read-audited \`GET /v1/admin/catalog\` now also
returns \`metadata_releases\` from the **shipped**, source-controlled
\`guardoath/catalog_metadata_releases.json\`. This adds no AWS/Stripe/provider
lookup, extra DynamoDB read, or separate audit receipt. The existing Cognito/MFA
authorization and protected read audit still apply. The current catalog source
and every recorded version must match full-registry forward and inverse SHA-256
fingerprints; a malformed, altered or stale chain fails closed as HTTP 503.
History is never inferred as zero when its provider is absent. The Catalog UI
shows the newest 20 version/target/changed-field entries, total count, and
the distinction between shipped source evidence and a signed approval or live
release check. A zero count is displayed only after verifying the baseline
source version. Signing, CI, deployment, rollback, financial and tenant changes
remain completely separate. Reversing a rule description requires a *new*
reviewed next-version source change; deleting release history is not rollback.

## Owner Overview: bounded saved subscription flags and read timing (2026-11-22)
The authenticated Overview reuses the same first, bounded DynamoDB tenant directory page already loaded for Tenants. Only when that one page is the **entire scanned population**, all identities are unique, and every non-demo tenant has a recognized saved subscription status, it shows **saved active subscription flags**. This is a count of records whose *saved* `subscription_status` equals `active`, **not** a number of provider-verified paying subscribers, current invoice customers, or confirmed revenue. Disabled tenants can still have stale active flags, and any unknown legacy status, duplicate, failed read, incomplete scan or continuation makes this metric **Unavailable**, never zero by assumption. The API now includes the UTC instant of the successful bounded DynamoDB read; it is not the tenant's last activity or a Stripe freshness attestation. The Overview labels read times only when the provider returned a valid UTC timestamp. No additional AWS read or Stripe call is made and the authoritative record is not mutated.


### Reviewed source metadata rollback (offline; never automatic)

A rollback of a **shipped** category/check/rule description is a new, monotonically
versioned source publication, not deletion of an old release, a live tenant
override or an immediate change on the website. In the owner Catalog, prepare
and independently review a fresh metadata-only draft reversing the latest
shipped source release's *entire* title/use-case diff. Export its reviewed
forward/rollback plan. After verifying the Git checkout matches the active
source registry, preview the guarded inverse:

```bash
python -m guardoath.catalog_source_release --plan reviewed-inverse.json --revert-last
```

Only after reviewing the proposed version and the approval/release checklist,
generate the tracked versioned source change with
`--write --confirm-registry-version <next-version>`. The command rejects
missing history, stale fingerprints, nonlatest targets, partial inversions,
reused review/draft identifiers and unrelated edits. It does **not** deploy,
sign the review, alter customer saved Off/actions, delete history or mark live
acceptance complete. Changes still require a reviewed non-forced commit, exact
source-SHA Tests/CodeQL/browser/security gates, rollback plan, and the 15
production checks; no AWS or Stripe operation is performed by the CLI.

## Concurrent owner tenant inspection (2026-11-22)
The authenticated **Usage**, **Verify Stripe**, and **Invoices** inspection actions now bind each pending display update to the latest tenant/action selection in the current owner session. If an earlier tenant read or provider error completes after a later selection, the obsolete success/failure cannot overwrite the newer result. Selecting invoices clears a prior tenant's subscription summary; selecting subscription verification clears prior invoice detail, so data for two different tenants cannot appear together as if reconciled. Sign-out continues to invalidate all pending display updates. These are browser protections, not additional server permissions: every underlying request remains independently Cognito/MFA-authorized and persistently audited according to the existing backend. No cost, provider write, entitlement grant or additional request is introduced.

## Owner Overview: recorded month-to-date validation volume (2026-11-22)
The Overview adds a recorded month-to-date requests value derived from the **already fetched** authenticated/audited DynamoDB tenant-directory response. A total is displayed only when the bounded scan covers the entire population, tenant IDs are unique/valid, every record has a nonnegative safe-integer current-month counter, and all usage months match the UTC read date. Overflow, omitted counters, mixed months, missing read time, duplication or pagination produce **Unavailable**; a completely verified empty population may legitimately total zero. The metric includes demos, and represents persisted validation request counts, **not** live traffic, billable Stripe usage, AWS costs or verified invoices. No new provider request, tenant mutation, policy, quota or financial-entitlement change is introduced.

## Owner Overview: saved allowance reached accounts (2026-11-22)
The Overview now counts non-demo tenant records whose saved UTC-month request
counter is at or above their stored monthly allowance, including accounts that
are currently disabled. This is a read-only advisory cost-control inventory,
not proof that usage was blocked, an AWS bill, an alert, a paid subscriber count
or a Stripe entitlement check. A stored zero allowance with zero recorded
requests is *at* its saved limit. The metric is shown only when the existing
bounded and audited tenant scan is the complete population, all record IDs
are unique and valid, every record has a current UTC-month safe-integer request
counter, and every saved allowance is a valid nonnegative integer at most
1,000,000,000. Missing legacy caps, malformed data, mixed months, partial
pages and scan failures show **Unavailable** rather than zero. The count is
recomputed on refresh and cleared on sign-out. It reuses the current DynamoDB
read with no extra Cognito/Stripe/AWS request or provider spend. It does not
modify the authoritative tenant record, overage policy or PII deadlines.

## Owner inspection before tenant rule action changes
The authenticated owner Catalog's **Set tenant rule** action now reads the selected tenant's current saved per-stage check action and that catalog rule's override **before** prompting for a write. The guarded `GET /v1/admin/tenants/{tenant_id}/rule-action?stage=input&rule_id=pii.email` returns only the exact saved revision, catalog rule/check ID, stage, check action, override, and a configuration eligibility label. It never returns tenant schemas, comparison payloads, secrets, key hashes or Stripe bindings. This is a signed-read-audited, Cognito-owner-and-MFA-only inspection using the same authoritative tenant record as the existing tenant management API. Missing, corrupt, mismatched or unvalidated tenant records return unavailable; disabled checks remain Off even if the per-rule override is Block. A configured rule is not proof of actual detector execution or provider availability. Owner UI refuses to mutate on unavailable or inconsistent inspection, explains the saved state and Off precedence, and uses that exact revision as the conditional audit-transaction write precondition. A race still fails closed with HTTP 409. No global catalog, tenant default, billing, detector or provider setting is changed by the read; the existing explicitly confirmed and audited POST remains the sole mutation path. This is not live AWS or 15-check acceptance evidence.

## Owner saved soft-cap exposure (2026-11-22)
The Tenants table now distinguishes **Block at saved cap** from **Allow and
record overage** using the existing persisted policy. A missing or invalid
saved setting is **Unavailable**, not a fabricated hard cap. The owner Overview
counts non-demo accounts at or above their allowance where the saved overage
policy still allows additional requests. It requires one complete, unique,
UTC-month-aligned bounded scan with valid caps, counters and policies. A single
unknown non-demo policy, partial page, invalid count or missing UTC read
returns **Unavailable**, not zero. A complete empty population may show zero.
This is saved policy exposure, not proof of actual blocking, bills, provider
requests, or a delivered alert. No new paid service, extra provider read,
customer policy change or Stripe write is introduced.


## Administrative usage integer precision boundary (2026-11-23)

DynamoDB records and authoritative tenant usage counters can contain Python
integers larger than a JavaScript client can represent exactly. The admin
DynamoDB directory now refuses pages containing a revision or usage counter
above 2^53−1 instead of displaying a rounded value or enabling an action with
ambiguous source revisions. Owner Usage similarly rejects such stored counts;
when a valid stored counter produces a larger *derived* pacing estimate,
utilization percentage or historical ratio, that derived number is unavailable
rather than rounded, while recorded over-limit status remains visible. These
are metadata/reporting safeguards; no tenant usage enforcement, provider call,
Cognito policy, Stripe data, stored record or 500-ms checker deadline changes.
Existing signed owner-authenticated API routes and browser integration continue
to display unavailable states for missing/untrusted projections. Full exact-SHA
CI, live /admin and production acceptance are separate requirements.


## Exact owner-mutation revisions

The authenticated owner API rejects unsafe tenant revisions for status, rule-action,
allowance-cap, API-key revocation and unpaid-subscription reconciliation requests.
A saved revision may be submitted only while both its integer value and the
resulting incremented revision are exactly representable by the browser
(`1` through `2^53 - 2`). Unrepresentable revisions fail validation
before a DynamoDB or provider mutation; do not round or retry them.
This affects no ordinary tenant checks, deadlines, subscription entitlements
or provider charges.

## Exact signed billing receipt correlation

The owner Reconcile unpaid action requires a confirmed, currently saved integer
allowance and an available signed-audit read-back. The UI only reports a verified
downgrade after independently reading the HMAC-verified audit event and matching
its exact event ID, tenant, prior/new revisions, provider state, original
allowance, zero new allowance and saved subscription state. Any mismatch is an
**indeterminate result**; no automatic reattempt is permitted. This does not
make Stripe state customer-editable, authorize refunds, or change entitlements
based on a browser return value.

Signed audit revisions now accept the exact safe browser integer range
(through 9,007,199,254,740,991) rather than rejecting otherwise valid
11–16 digit revisions. Larger/noncanonical DynamoDB numbers remain unavailable;
the owner mutation request already refuses any increment that would exceed
the browser-exact range. The prior per-operation limit/count bounds remain.


## Owner tenant detail and usage precision parity (2026-11-23)

The authenticated single-tenant Detail and Usage API paths now apply the same
JavaScript-exact integer bound (2^53 - 1) already enforced by the bounded
DynamoDB directory. This applies to stored tenant revisions and every persisted
usage-month counter, not only the current period. The exact upper bound remains
readable; a larger or malformed value makes the entire requested view unavailable
(HTTP 503) instead of returning rounded revisions or counters that could mislead
the browser, selected-tenant controls or signed audit reconciliation. Mutation
revision limits are unchanged and remain stricter to allow exact increments.
The UTC-month metadata and unknown-cost semantics are unchanged. No additional
provider calls, cloud expenditure, tenant writes, check timeouts or entitlements
are introduced. These source/API regressions do not prove live /admin deployment.


## Provider-verified Stripe payment collection pause (2026-11-23)

The owner **Verify Stripe** inspection now distinguishes a provider subscription
that is `active` from its separate `pause_collection` setting. A verified
non-null pause displays **Payment collection: Paused**, its recognized Stripe
behavior (`keep_as_draft`, `mark_uncollectible` or `void`), and the recorded
resume time or **Not scheduled**. A verified explicit null displays **Not
paused**; an omitted field is **Unavailable**, never a fabricated assertion
that Stripe is collecting payment. An otherwise consistent saved/provider
status receives a **review** finding when collection is paused; existing
critical billing-state disagreements keep their precedence. Malformed pause
metadata makes subscription verification unavailable. This uses the same
existing one owner-authorized, audited subscription read; it does not fetch
invoices, alter paid entitlement, resume collection, charge/refund anyone,
or change tenant source-of-truth settings. Active subscription status is not
proof of payments received or of live revenue.


## Provider-verified collection method and versioned billing period (2026-11-23)

The authenticated **Verify Stripe** action now labels the independently verified
subscription's invoice collection method as **Automatic charge**, **Send invoice**,
or **Unavailable**. Sending an invoice does not prove that it was paid: when the
saved and provider entitlement statuses otherwise agree, manual invoicing is a
**review** finding. Existing critical provider drift and payment pause findings
retain precedence. Missing method is unknown; an invalid provider value makes
the read unavailable. Neither collection method is a revenue or entitlement
write, nor proof of money collected.

For the pinned Stripe 2025-02-24 API, the subscription-level current period
end remains supported. A newer Stripe representation can place it on the sole
verified subscription item. The owner panel accepts the single item date or
falls back to a valid subscription-level date, labels the source, and shows
**Unavailable** for malformed, ambiguous, conflicting or absent dates. This
does not change the application's pinned provider API version, billing
entitlements, tenant records, Stripe costs, invoices or 500-ms validation limit.


## Provider-verified scheduled subscription cancellation (2026-11-23)

Owner **Verify Stripe** now surfaces an active subscription's independently
verified `cancel_at_period_end` flag as **Scheduled at current period end**,
**Not scheduled**, or **Unavailable**. A UTC effective date is displayed only
when the same bound provider snapshot contains a valid, nonconflicting
subscription/subscription-item period end. An absent, malformed or contradictory
period produces **Unavailable** for the effective date, never a fabricated
renewal or automatic access-revocation date. When the verified active provider
and saved entitlement otherwise agree, the scheduled end is a **review**
finding; critical provider drift, payment pause and manual invoice review
still take precedence. The current verified subscription can remain active
until Stripe actually reconciles its status. No entitlement, invoice, customer
account, catalog, tenant record or provider is mutated. No extra Stripe call or
charge, AWS spend, original deadline, or MFA gate is changed.


Stripe's `cancel_at` is independent of `cancel_at_period_end`: a verified
future cancellation may be scheduled for a custom date while
`cancel_at_period_end` is false. The owner view distinguishes **Scheduled for
provider date** from **Scheduled at current period end** and emits a separate
read-only review finding. **Not scheduled** requires explicit false
`cancel_at_period_end` and explicit null `cancel_at`; an omitted field is
**Unavailable**, not evidence that a subscription will renew. Malformed
`cancel_at` blocks the advisory read; a disagreement between period-end and
direct cancellation timestamps hides the effective date. All provider binding
and existing entitlement checks still run first.


## Verified invoice next-attempt observations
Owner **Tenants → Invoices** shows a count, limited to the displayed page, of open positive-balance automatic-collection invoices with a provider-recorded earlier attempt and next-attempt timestamp. It never infers payment failure, overdue debt, a future retry or recognized revenue from this signal. Missing attempt metadata and manually invoiced or paid rows do not qualify. The browser independently recomputes and fails closed on inconsistent count data. Separate page observations are never accumulated into a financial total.


## Concurrent-safe source-controlled catalog metadata release

The owner Catalog review UI produces a metadata-only source plan after a signed
draft, signed review, source fingerprint check and inverse/rollback preview.
Run python -m guardoath.catalog_source_release --plan reviewed-plan.json as a
read-only dry run. A source candidate may be written only with --write and
--confirm-registry-version NEXT_VERSION, followed by independent source review,
benign/adversarial/timeout tests, exact-SHA CI, guarded deployment and the
15 live checks. The command does not publish to production or authorize a reviewer.

The offline source writer now acquires an exclusive same-directory lock before
reading the tracked release history and computing the next version. A second
cooperating writer fails closed. It compares the exact original history bytes
again before atomic replacement, refusing even a same-version out-of-band edit
rather than overwriting other work. Failures clean up its temporary source file
and its lock. A pre-existing lock is never stolen or automatically removed:
first confirm no writer remains active and inspect the source state before any
manual cleanup. This cooperative source-code guard is not a substitute for
Git main-SHA synchronization, independent source review, or an AWS transaction.

### Owner directory out-of-order response protection (November 23, 2026)

The authenticated Users and Tenants lists now bind provider replies to the selected filter and newest request within the current owner session. Editing a filter withdraws displayed rows and continuation tokens; older successes or failures cannot supersede a newer selection, including during Refresh. A failed current tenant page clears its previously shown rows. The Cognito disable/enable UI permits only one action in flight through exact-account preflight and immutable signed outcome verification. Server-side owner/MFA authorization, signed audit, provider idempotency and tenant source of truth are unchanged. This additional browser guard does not prove live Cognito behavior or production deployment.


## Owner refresh failure and concurrent reads (2026-11-23)

The authenticated /admin workspace withdraws its preceding overview metrics, user and tenant rows, catalog data, navigation cursors and unverified mutation affordances as soon as a new full refresh begins. A failed core overview, tenant, catalog or capabilities read leaves **Unavailable** source labels and refuses to show the older snapshot as current. A later successful owner read restores the real source-backed views. A delayed older refresh, whether successful or failed, cannot erase a newer response; any independently pending Cognito user status mutation retains its single-flight guard. No tenant, billing, provider, AWS resource or validation policy is changed. Playwright covers failure, recovery and response reordering; exact-source CI and protected production MFA acceptance remain required.


### Source release JSON ambiguity protection (November 23, 2026)

The offline reviewed-plan reader and tracked catalog release-history reader reject duplicate
JSON object keys at every nesting level and non-finite JSON numbers (NaN or Infinity).
The ordinary Python JSON decoder would otherwise accept these ambiguous inputs and silently
keep only the last duplicate field. Such input now fails before catalog-provider inspection
or any source write. This does not make an unsigned exported review an authorization;
the independent review, exact-source CI, rollback and 15 live checks remain mandatory.

## Owner detail source parity (2026-11-23)

The authenticated owner browser rejects a Usage response without the exact selected `usage.tenant_id`, and Stripe subscription or invoice responses without the selected envelope `tenant_id`. Even HTTP 200 with a missing or different ID displays unavailable information rather than another tenant's request counters, paid state or invoice. The server already independently binds source tenant records before provider reads. This extra browser invariant makes no Stripe/AWS writes or requests, changes no entitlement and does not replace the owner Cognito/MFA check. Authenticated browser regressions include matching, cross-tenant and missing-ID responses; live source parity and exact-SHA release gates are still required.


## Tenant-directory source integrity (November 23, 2026)

The owner Users & Tenants directory reads the authoritative DynamoDB tenant record,
rather than trusting a client-supplied account or subscription status. An account
record containing duplicate JSON keys at any depth, nonstandard NaN/Infinity,
or a floating-point literal that overflows to infinity now fails the entire
bounded page with provider-unavailable status. It does not silently choose the
last copy of `demo`, `tenant_id`, or `subscription_status`. Successful
pages still show only redacted metadata and identify subscription state as
persisted, not freshly reconciled with Stripe. No tenant policies are changed.


## Provider invoice JSON integrity (2026-11-23)
The owner Tenants → Invoices provider adapter now rejects duplicate keys anywhere
in the Stripe invoice response, non-finite JSON constants, and floating-point
overflows before projecting subscription-linked payment information. Previously,
Python's permissive JSON decoder could silently use the last of two conflicting
invoice statuses or amounts. Ordinary finite decimal provider metadata remains
accepted; actual financial fields still require bounded integer minor units.
An ambiguous provider result yields Unavailable/503, not fabricated paid
status, a balance, a refund, or entitlement. The browser continues to use its
existing owner-authenticated invoice view; no Stripe write or additional read is
introduced. This is a source safeguard, not live provider reconciliation evidence.


### Strict saved tenant metadata timestamps (November 23, 2026)

Both the authenticated tenant directory and individual tenant detail now validate a
present authoritative `updated_at` as a real calendar UTC timestamp in
`YYYY-MM-DDTHH:MM:SSZ` format, rejecting impossible days, hour 24,
fractional timestamps, alternative offsets, and incorrect types. A missing
legacy timestamp remains explicitly unknown; no recent/fresh claim is inferred.
A malformed present timestamp fails the protected read rather than displaying
false freshness. This is separate from Stripe verification or AWS live costs.


### Strict Stripe subscription source decoding (November 23, 2026)

The shared server-side Stripe subscription adapter used by owner Verify Stripe,
audited entitlement reconciliation and customer discovery now rejects duplicate
JSON keys at any depth, non-finite numeric constants, overflowed decimals, and
non-object responses. Ordinary finite provider decimals remain acceptable.
Subscription discovery also rejects malformed status types; its independent GET
must confirm the same test/live environment as the server credential. An
ambiguous or mismatched response cannot authorize activation or an owner
downgrade, and the authenticated owner UI reports verification unavailable.
This adds no provider calls, financial mutations, credentials, AWS cost, or
customer policy changes. A verified subscription is still not evidence of a
paid invoice, refunds, collected revenue, or complete billing administration.


## Owner twelve-month recorded-usage history (2026-11-23)

The authenticated Tenants → Usage workspace now displays twelve consecutive UTC calendar months from the same authoritative record, newest first. The current month is partial; a missing month is explicitly Unknown, not zero. A saved zero remains zero. Future-dated and out-of-window entries are excluded from this display without changing the saved record. The API retains the existing sparse history field for compatibility and adds `calendar_usage_months`. The browser validates the full bounded sequence and current-month counter consistency before displaying it; malformed history is unavailable. No additional provider or database reads are introduced. This is recorded request history, not a bill, cost allocation, or proof of complete historical traffic. AWS financial data remains unavailable until verified.


### Account and audit selection consistency (November 23, 2026)
Exact Cognito account inspection, signed account-action receipt lookup, and audit receipt lookup are bound to the latest operator selection in the authenticated browser. Editing the selected input or refreshing the workspace invalidates older pending reads. A late result or failure cannot overwrite the current account or receipt; a new Cognito action also invalidates earlier account/receipt inspections. Server authentication and persistent audit remain authoritative. No extra provider read is introduced.


### Tenant directory quota triage (November 23, 2026)

The authenticated Tenants table now shows a per-record **Saved quota risk (UTC)**
column next to usage and the saved overage policy. It classifies below 80%, watch
at 80%, near at 95%, at allowance, and above allowance using the authoritative
saved request counter and allowance in the directory's own UTC read month.
Rows with an unknown, malformed, mixed-month or unsafe counter/cap are unavailable.
A bounded partial page can still show individual valid records; it does not
establish population totals. The column is advisory, not a persistent alert,
live traffic, invoice or AWS cost; allow-and-record is not hard blocking.
No new database or provider requests are made.


### Saved quota risk triage (current page)

In Tenants, choose **Saved quota risk (this page only)** to show Watch or
higher, Near allowance or higher, At or above allowance, or Unavailable.
The filter operates on the already authenticated, audited directory page;
it issues no additional request, does not modify tenant records, and is not
a global search or alert. Continue with Next page even when none of the
current rows match. Unknown counters are not treated as zero or safe.
The filter is reset on sign-out. AWS spend and live billing remain unavailable.

## Protected invoice history pages and status filters

Choose **Invoices** beside a tenant. The existing Billing workspace reads the
newest five invoices for that independently verified saved subscription. Choose
**Older invoices** for the next five, select an invoice status to start a new
filtered first page, or use **Restart invoice history** to reread page one with
the selected filter. No automatic background crawl or financial mutation runs.

The existing authenticated endpoint accepts these bounded query parameters:

| Parameter | Contract |
| --- | --- |
| `status` | Omit for all; otherwise exactly `open`, `uncollectible`, `paid`, `draft` or `void` |
| `cursor` | Omit for the first page; otherwise use only the opaque `history.next_cursor` returned for this owner, tenant and filter |

Every page independently checks owner authorization and current MFA, persists a
signed `stripe.invoices` read-attempt receipt, loads the authoritative tenant,
validates the cursor against its saved billing binding, verifies the subscription
with Stripe, and performs one invoice-list GET. The adapter retains the existing
pinned `2025-02-24.acacia` contract, strict JSON, fixed Stripe host, no redirects,
five-row/256-KiB response bound and five-second timeout per provider read.
See [pinned invoice list API](https://docs.stripe.com/api/invoices/list?api-version=2025-02-24.acacia)
and [Stripe pagination](https://docs.stripe.com/api/pagination).

The `ic1.` cursor uses authenticated encryption with a separately derived key;
raw invoice, customer and subscription IDs are not exposed. Its context includes
owner, tenant, saved billing IDs, configured price/environment, filter and page
size. The original 15-minute expiry persists across pages. Changing the billing
binding or filter, using another owner's cursor, tampering or expiry refuses
the page before either Stripe read. Ordinary saved revision changes do not
invalidate an unchanged billing identity. A fresh provider verification remains
mandatory. The cursor is held in tab memory, not browser storage.

Each successful response identifies `page_number`, `status_filter`, `next_cursor`,
`has_more`, a UTC read time and the signed read receipt. A page with more results
must contain a usable next cursor and at least one invoice. Rows must preserve
provider order and match the customer, subscription, environment and filter;
duplicates within the page and the prior continuation anchor are refused.
Equal creation timestamps are allowed. The service does not retain every prior
invoice ID or claim a frozen, globally deduplicated traversal.

All amount and collection-attempt summaries apply only to the displayed page.
The first unfiltered page retains `verified_first_page_only`; later or filtered
pages use `verified_selected_page_only`. `complete_history` can be true only for
an unfiltered first page with no additional results. The last continuation page
is not complete invoice history. Separate reads can observe changing amounts or
statuses and are never added into a revenue, receivables or margin total.
Currencies remain separate integer minor units without conversion.

HTTP 400 means the cursor/selection is invalid or expired; restart the history.
HTTP 429 means the invoice-read budget is exhausted; wait before restarting.
HTTP 503 means verification is unavailable. All clear the previous page's
amounts and continuation. No raw provider error body is displayed. The budget
is 20 invoice-page requests per owner per minute in a warm process, with bounded
owner tracking; it is not a distributed rate limit and does not replace edge or
provider controls. Each accepted read can make one subscription GET plus one
invoice GET. Audit and owner identity checks precede that budget.

Controls prevent duplicate in-flight page requests while permitting a different
tenant selection. Refresh, logout, subscription inspection and confirmed billing
reconciliation clear the old page. Selection/auth epochs suppress late results;
an old session's late 401/403 cannot sign out a new session. Read-only API tests,
the actual mounted owner browser journey and focused fault/race/mobile tests are
part of the existing test gates. Production financial evidence still requires
real provider credentials and deployed owner/MFA acceptance.

The implemented cancellation contract and remaining financial work are preserved
in [admin-financial-operations-design.md](admin-financial-operations-design.md).
Refunds, credits and plan changes remain separate capabilities.

## Owner period-end cancellation review and recovery (2026-11-24)

The existing Billing workspace supports cancellation review, confirmed
period-end scheduling and signed historical receipt lookup. This source sets
`ENABLE_NEW_CANCELLATION_STATE=True` after the compatible reader was deployed
in 762e7b6. Scheduling remains unavailable without the dedicated provider/account
and signed audit configuration. The explicit disabled-reader tests still require
an unmounted scheduling route and refusal at every write boundary. No
environment variable can bypass the source phase.

### Explicit configuration and narrow admission

Review requires `GUARDOATH_ADMIN_STRIPE_WRITE_KEY` and
`GUARDOATH_ADMIN_STRIPE_ACCOUNT_ID` together, plus the existing Stripe plan/key
configuration and independent signed audit store. These are server-only secure
environment values; there is no fallback from the new credential to the existing
read key. The new adapter narrowly accepts restricted or secret test/live key
forms and requires mode agreement with the configured Stripe reader. Provide
only the account/subscription read and subscription update permissions needed
for this operation. No credential appears in the UI, receipt projection or
provider metadata. Receipt lookup needs the signed audit store but no Stripe
write credential. Invalid partial configuration prevents admin initialization.

Choose **Review cancellation** beside the bound tenant. The API first verifies
owner/Cognito/MFA and origin and persists a signed protected-read attempt. It
strongly reads the actual tenant record, then checks the configured Stripe
account and the exact saved subscription with the same cancellation credential.
No subscription discovery or browser-selected provider ID is accepted.

The initial supported state is one active, automatically collected item of
quantity one at the configured price, explicitly unpaused, with no schedule,
pending update or existing cancellation. Item and subscription periods must
agree when both are present. Trial, manual-invoice and other unsupported states
remain inspectable through existing read views but cannot enter this operation.
The review shows its observation, scheduled end and expiry in UTC. Its encrypted
reference lasts at most 180 seconds and binds the exact owner, tenant document,
revision, provider state and prior target control. Refresh or selection changes
withdraw the review; a changed provider state requires a fresh review.

### Confirmed scheduling

The writer requires the displayed `SCHEDULE CANCELLATION <tenant_id>` text,
bounded reason and action reference. It never accepts arbitrary Stripe fields,
price, quantity, invoice, refund or proration parameters. A target control and
immutable signed intent are inserted atomically; a second transaction checks
the exact tenant JSON/revision while acquiring dispatch ownership. Only an
acknowledged successful transition can issue the one fixed
`cancel_at_period_end=true` request. The pinned adapter refuses redirects and
automatic retries, validates its reply, then independently rereads account and
subscription before storing a verified outcome. The browser independently
reopens the signed receipt before showing verified success.

Scheduling changes no tenant status, entitlement, cap, key, usage counter or
customer policy. Trusted reconciliation remains responsible for later access
changes. A scheduled end is neither immediate cancellation nor a guarantee that
no further invoice can arise. See Stripe's
[cancellation behavior](https://docs.stripe.com/billing/subscriptions/cancel).
Another Stripe integration can still race the local observation; GuardOath does
not claim a provider-wide compare-and-set.

### Uncertainty and historical recovery

Keep the action reference when the page reports unverified or indeterminate.
Sign in as the original owner and use the Billing receipt lookup with the tenant
and original reference; action IDs are owner-bound. A conflicting new request
does not disclose another owner's held action reference. This
reads signed historical intent/outcome only, with no provider read or retry.
A missing intent means not found; a retained intent without verified outcome
means indeterminate. A verified linked outcome proves the recorded action, not
the subscription's current state. Existing **Verify Stripe** provides a separate
current observation and cannot manufacture a historical action receipt.

The page blocks duplicate in-flight dispatch across refresh, withdraws stale
previews on confirmed reconciliation, and suppresses results from older
selections or sessions. Signing out does not cancel a server operation. Durable
unknown claims never expire automatically and cannot be released through this
UI. There is no automatic repair or provider retry. A later generation is
available only after a verified prior outcome and a fresh eligible review.

The service uses an eight-second maximum coordination budget, reduced using
trusted Lambda remaining time, with four seconds required before reservation
and dispatch and at most a one-second provider socket timeout. These checks do
not guarantee completion before termination: blocking AWS or body reads and
persistence failures can still leave a retained indeterminate operation. The
15-second Lambda timeout and 500-ms PII deadline are unchanged. Review/admission
also shares a best-effort 20-inspections-per-owner-per-minute warm-process budget;
this is not distributed rate limiting.

The contract is `stripe-period-end-cancellation-v1`, independent of cap
provenance. Dedicated financial envelopes leave the shared ordinary audit schema
unchanged. Infrastructure adds only `dynamodb:ConditionCheckItem` on the existing
tenant table under the existing admin-mutations policy condition. No new table,
capacity or paid provider is added. All exact-source CI, source parity, compatible
rollback and 15 live checks still apply. Local synthetic provider/database
acceptance is not evidence of AWS deployment or live financial data.

## Catalog startup and actual source-reversal acceptance (2026-11-24)

The application now interprets shipped catalog history with the same strict JSON
decoder as the existing source-release tool. Duplicate keys at any depth,
nonfinite constants, overflowing numbers and malformed UTF-8 are refused at
startup; the actual history read is bounded to 256 KiB plus one byte before size
rejection. Valid finite JSON and Unicode remain supported. This closes a
runtime/source-tool disagreement without changing any shipped definition or
customer policy. Source locks, atomic replacement and version/reference checks
remain intact.

The source acceptance now materializes a real reviewed rule-title change and its
freshly reviewed inverse in a copied application. Actual authenticated owner
draft/review/manifest routes, reopened signed receipts, the real CLI, three fresh
app starts and both customer/owner catalog projections are exercised. The real
shared Engine verifies positive/benign Input/Output outcomes and saved per-rule
and parent-check Off behavior at each stage; tenant records remain byte-for-byte
unchanged. The original repository history is not edited by this acceptance.

This is local source/app/API/Engine evidence with synthetic external transports,
not a live publication action or a Chrome/customer-login result. The subsequent
request/preparation integration below preserves separate signed review and
publication authority. The full implementation and remaining release lifecycle
are recorded in [the catalog publication record](admin-catalog-publication-design.md).
General category/check/rule authoring and production acceptance remain unfinished.

## Owner catalog request review and preparation history (2026-11-25)

This activation source supports and introduces `catalog-publication-v1` and
`catalog-publication-lifecycle-v1`. Both
`ENABLE_NEW_CATALOG_PUBLICATION_STATE` and
`ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE` are `True`. In **Catalog**, first save and
independently inspect a signed description draft, record its signed review, and
verify the metadata manifest. **Review publication request** then regenerates
the exact proposal from the real source catalog/history and the same owner's
signed evidence. Supported edits are limited to the `title` and `use_case` of
one existing rule. A signed review or downloaded source plan alone never
creates a publication request.

The accepted d81db4d compatibility baseline exposed review/history with request
writes disabled. This later source mounts `POST /v1/admin/catalog/publications`
and enables its confirmed request control when the existing independent audit
service is configured. Separate feature
fields describe `catalog_publication_preview`, `catalog_publication_requests`
and `catalog_publication_history`; broad `rule_publishing` remains false.
There is no environment variable that activates the writer. The API, service,
state adapter and external worker each enforce the source gate.

Both source gates are enabled together after the accepted d81db4d deployment
established reader support for both catalog contracts. The actual backup ZIP
must support both introduced contracts before this release can deploy; no old
unsupported backup or environment override can substitute. The lifecycle includes bounded current-request
selection, remote publication, complete CI/deployment evidence and verified
terminal control. An operator must run it; the browser does not start a
background worker or provision any scheduler.

### Separate confirmed publication request

After reviewing the current target/version, select **Publish metadata** or
**Reverse latest release**. Reversal requires a fresh review containing the exact
inverse of the latest source-history entry and appends another higher version.
Enter the displayed `PUBLISH` or `REVERSE` confirmation with the review's final
eight characters, provide a reason, and retain the generated action reference.
Only that explicit confirmation can reserve a request; it does not deploy.

The service ignores browser or environment claims of source authority. It reads
the strict bounded packaged `_release_source.json`, verifies the actual release
contract manifest, rereads the signed draft/review, regenerates the full metadata
manifest and compiler plan, and checks current global control. The immutable
intent records those exact source, review, plan, target and version bindings.
Intent insertion and control reservation are atomic in the existing audit table;
no new resource is provisioned. Runtime uses the same audited client/table and
draft store already mounted for the owner console, with lazy trusted source and
history providers.

All publication routes require verified owner membership and fresh MFA.
POST routes require the existing same-origin policy. A signed `catalog.draft`
read intent precedes proposal/request reads, and a signed `audit.receipt` read
intent precedes historical inspection. Audit failure blocks state/source access.
The browser independently rereads the request after its POST and checks its
review, source, target, metadata, reason and stage before displaying it.

### Lost reply and historical recovery

Use **Recover publication request** with the original action reference. Its
`POST /v1/admin/catalog/publications/recover` endpoint is read-only. The exact
request can also be inspected with
`GET /v1/admin/catalog/publications/{request_id}`. These readers verify the
original signed records and work without the current source marker or worker
credentials. Exact same-reference replay returns the original verified request;
altered request content at that reference conflicts.

The browser retains the attempted reference across a workspace refresh and
prevents another submission after a lost response. Logout clears it and
suppresses late results; keep the reference in the approved operational record
before leaving the session. An unknown/missing receipt does not authorize a new
request, and no recovery read dispatches source preparation. Refresh, changed
selections and expired sessions cannot restore stale request authority.

| Verified stage | Meaning and remaining work |
| --- | --- |
| Requested | Immutable intent and global control are recorded; an operator must invoke the bounded release worker |
| Preparing | One worker obtained the acknowledged preparation claim; interrupted or uncertain work cannot be retried automatically |
| Local source prepared | Exact retained candidate, parent and signed history receipt exist; publication and deployment are not yet established |
| Source publication in progress | One conditional claim authorized the fixed non-forced push; an unknown result retains control |
| Remote source published | The exact candidate is recorded on the fixed repository main; CI and deployment remain separate |
| Release jobs verified | All required individual jobs in all seven exact-candidate workflows succeeded |
| Deployment in progress | One claim and fresh invocation ID authorized the guarded script; a lost result cannot authorize another upload |
| Deployment source/assets verified | The exact Lambda code, backend source, full dashboard and every linked asset are bound to the operation receipt |
| Release complete | The signed terminal chain permits a fresh next proposal; this is not all-fifteen-area production acceptance |

The original preparation receipt is immutable and continues to describe only
preparation. Subsequent progress comes from distinct signed lifecycle receipts.
`tenant_policies_changed` and `detector_behavior_changed` remain false. Even the
terminal receipt reports `production_live_acceptance=false`.

After completion, **Review another release** independently rereads the completed
request and current control, then starts a new draft/review. The next proposal
must match the actually deployed source, registry version and complete metadata
fingerprint. A stale app or old confirmation cannot reserve a successor. Earlier
requests remain recoverable by their original owner/reference after control moves
to the next request.

### Bounded external release worker

`catalog_publication_worker.prepare_publication` is an external, exact-ID,
prepare-only operation. It verifies the repository origin and exact clean base,
reopens signed evidence, acquires the global claim, creates a detached worktree,
runs the existing compiler plus actual source/app/Engine acceptance, and retains
a local candidate commit. Git and subprocess privileges are outside the
request-serving Lambda. It never pushes, schedules CI or deploys. The standalone
CLI accepts a private audit-key file only where POSIX permission checks are
available; it deliberately refuses that key-file path on Windows. The callable
worker remains portable with correctly injected signed state.

`catalog_release_worker` extends the preparation primitive through the actual
fixed-origin non-forced push, complete exact-source GitHub jobs and the candidate's
real guarded deployment script. Its `--next` mode reads only the one signed global
control and handles at most that request; `--prerequisites` and `--help` describe
the required private operator workspace, existing Git/GitHub access, Python 3.13,
PowerShell, AWS CLI/profile and trusted audit-key intake. There is no table scan,
perpetual polling process or newly provisioned cloud scheduler.

Use `--production-config` with the existing production profile to load the
configured audit table and derive its signing key in memory. The bootstrap pins
AWS account `839000214942`, region `us-east-1` and function `guardoath-site`,
requires a completed configuration with owner administration enabled, and refuses
an invalid audit table or one equal to `GUARDOATH_TENANT_TABLE`. It derives the
key from `GUARDOATH_ADMIN_CURSOR_HMAC_KEY` with the same audit label as
`admin_runtime`; it never writes or prints that key or the Lambda environment.
Do not combine this mode with `--audit-table` or `--audit-key-file`.

On the operator's POSIX release host, with Python 3.13, PowerShell 7 and AWS CLI
already on `PATH`, the bounded entry points are:

```sh
export POWERSHELL_TELEMETRY_OPTOUT=1
python -m guardoath.catalog_release_worker --prerequisites
python -m guardoath.catalog_release_worker --next --production-config --aws-profile collectiverse-prod --repository /path/to/guardoath --work-root /private/operator/catalog-releases
```

The paths are operator configuration, never browser-supplied values. Preserve the
private work root and its retained candidates between invocations. A request
waiting for CI remains at its recorded stage; a later invocation can observe the
candidate's actual completed jobs. This does not authorize retrying an uncertain
push or deployment. The receipt directory requires POSIX ownership/mode checks.

Git needs existing fixed-origin push credentials. A non-mutating dry run detects
denied push access before the publication claim. The API reader and guarded
deployer honor the operator's existing `GH_TOKEN` or `GITHUB_TOKEN`, send it only
to the fixed GitHub HTTPS host, refuse redirects and suppress provider error
bodies. A connected GitHub app does not automatically supply shell credentials.
Missing or expired provider authorization is an operator setup problem; it must
not be described as a completed publication.

The script writes a fresh operation-bound machine receipt only after successful
code/configuration and full source/asset verification. Normal terminal completion
uses that just-verified receipt without running the same AWS/asset pass again.
A separately resumed deployed stage needs readback because the earlier call ended
before terminal evidence persisted. AWS upload/completion revisions remain
separate; an observed later revision never grants rollback authority.

Global control is replaced only by the next legitimate request's atomic,
full-document transaction after a verified terminal chain. Nothing deletes,
expires or resets it to bypass an unknown outcome. Retain interrupted/unknown
evidence and its original reference. CI failure, incomplete evidence, a lost
push/deployment acknowledgement or changed source cannot silently release control
or dispatch another attempt.

Current local acceptance includes the actual activated source defaults, real
mounted auth/API/services and reopened signed storage with synthetic external
transports. The explicit disabled compatibility fixture remains covered. Actual Chrome journeys are added to
the existing baseline/writer suites; local browser execution remains blocked by
Chromium availability and OS socket restrictions. Exact-source CI, compatible
rollback and all 15 live checks remain required. Tenant records, saved per-stage
Off settings, attachments, detector behavior, payment entitlements and the
original 500-ms PII deadline remain unchanged.
