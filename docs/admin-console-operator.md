# GuardOath operator administration

The new `/admin` route redirects to `/admin/login` only when the admin feature is enabled. It is **disabled by default**. Ordinary customer API keys and Cognito sessions never grant owner privileges.

## Authorization
Set `GUARDOATH_ADMIN_ENABLED=1` only after establishing a separate public Cognito admin OAuth client with PKCE and callback `https://guardoath.com/admin/login`, in the existing trusted pool, and verifying independently fetched `GuardOathOwners` membership and signed current-login MFA evidence. Required: `GUARDOATH_ADMIN_CLIENT_ID`, `GUARDOATH_ADMIN_COGNITO_DOMAIN`, `GUARDOATH_ADMIN_CURSOR_HMAC_KEY` (independent 32–64 random bytes, hex). An invalid/missing configuration fails closed at startup. CloudFront forwards `X-GuardOath-Admin-Session`; `Authorization` is reserved for AWS origin signing.

## Capabilities
Read-only overview, bounded tenant directory and current-month usage, category/check/rule catalog, and on-demand provider-verified subscription lookup when existing `GUARDOATH_STRIPE_SECRET_KEY` is configured. Stripe snapshots do not change entitlement or expose payment details. Costs and revenue are unavailable without a separately approved verified data source.

Optional `GUARDOATH_ADMIN_AUDIT_TABLE` enables owner tenant suspension/reactivation, single tenant rule action updates, and downward-only allowance caps with enforced Block overage. **Do not enable** until a separate append-only DynamoDB audit table, least-privilege transaction IAM and operational restore procedure exist. Every mutation requires typed tenant confirmation, reason, expected revision and atomic update + audit. No billing changes, refunds or versioned global catalog publication are implemented yet.

## Release gate
Run full repo and browser tests plus exact-SHA Tests (13), CodeQL (3), Field Comparisons Evidence (2), Trusted Reference Acceptance (1), PII Reliability Gate (3); maintain 500-ms checker limit and the existing verified deploy script rollback and 15 live acceptance checks. Unresolved issue #30 must be reported, never silently closed. A GitHub commit is not an AWS deployment.

## Opt-in infrastructure provisioning
The deployment template includes default-off `EnableAdminConsole` and a separate `AdminCursorHmacKey` NoEcho parameter. Only explicitly enabling the feature provisions an isolated Cognito OAuth code/PKCE client, the `GuardOathOwners` group, and pool-scoped read-only Cognito IAM permissions. The template rejects activation without an independent random 32–64 byte hex key. It creates no owner account or group assignment and does not provision a mutation audit table.

**Do not activate from a source commit.** First verify stack change set and costs, the existing SOFTWARE_TOKEN_MFA policy, an explicitly enrolled authorized owner, signed `amr` evidence from the **current MFA login** (not mere MFA enrollment), CloudFront forwarding `X-GuardOath-Admin-Session`, exact-source CI, rollback and all 15 live acceptance checks. If Cognito does not emit the expected signed per-login `amr`, the verifier correctly denies access: do not weaken it. Until this evidence is available, leave `EnableAdminConsole=0`.


## Cognito user directory
The owner-only UI and \`GET /v1/admin/users\` page existing Cognito users through the server identity service. The \`email\`-prefix filter and page size (1–50) are bounded. Continuation tokens are HMAC-signed and bound to pool, query, limit and a 15-minute expiry; invalid/expired cursors are refused before a provider read. Only masked email hints, account confirmation/enabled flags, recognized status and UTC creation/last-modified timestamps leave the server. Total count is unknown, not zero; provider/IAM failures return unavailable. One provider call is made per page. This is a read-only UI; it neither alters Cognito identity nor authorizes tenant access or payment, and is not a substitute for persistent owner-read audit or future user writes. No new paid services or AWS resources are introduced.

## Persistent audit infrastructure (opt-in, default disabled)
`EnableAdminMutations=0` creates **no** additional resources. Explicitly setting it to `1` requires `EnableAdminConsole=1` and provisions a **separate** DynamoDB `AdminAuditTable` with 1 provisioned read and 1 write capacity unit, stable `event_id` partition key, deletion protection and Retain policies. CloudFormation rejects this configuration when the existing tenant table is at 25 RCUs or WCUs. This option consumes additional AWS capacity: do not enable without reviewing the stack change set, current account free allowance, cost budget and deletion/restore procedure.

The conditional Lambda environment variable `GUARDOATH_ADMIN_AUDIT_TABLE` and an IAM permission limited to `dynamodb:PutItem` on the dedicated audit table activate the existing atomic tenant revision + audit transaction pathway. The runtime is not authorized to update/delete/scan audit items. No Cognito user-account mutation, refund or catalog publishing route is enabled by this infrastructure. Replaying transactions, partial provider failures and independent read-access auditing require separate acceptance before exposing more operations.

## MFA provenance and sign-in freshness
Owner authorization requires a **signed** Cognito access-token `amr` array containing the explicit `mfa` marker, plus an integer signed `auth_time` no older than 15 minutes (30 seconds of forward clock skew). A single `otp`, `totp` or `software_token` marker is **not** proof of multi-factor sign-in. Refresh tokens retain the original `auth_time`, so a refreshed token cannot renew the original 15-minute administrative authentication window. The owner group, enabled account, subject and live token validity are independently checked on every request.

**Current AWS configuration limit:** The existing `UserPoolTier=LITE` and `UserPoolDomain.ManagedLoginVersion=1` use Cognito's classic hosted UI. AWS documents that classic hosted UI does not support reliable per-login ACR/AMR MFA evidence (its values are defaults even after MFA), and managed login requires Essentials or Plus. Consequently, a properly hardened admin server may deny an otherwise enrolled owner on this tier. Do not synthesize `amr`, accept MFA enrollment as proof, or silently move the customer pool to a paid feature plan. Production activation needs a separately reviewed, compatible MFA attestation mechanism and its AWS cost/migration impact.

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
Cognito user-list pagination cursors are now version 2 and bind the signing pool, requested email filter, page limit, expiration **and the independently verified administrator subject**. A cursor issued under one authorized owner cannot be replayed by another owner; mismatches fail locally before another Cognito read. The server derives the owner subject from its trusted Cognito/MFA verification, not browser query parameters. Old cursors fail closed. No customer data or credentials are embedded in a cursor beyond the opaque provider continuation token.


## Owner-scoped tenant pagination and unknown allowances
Tenant-directory HMAC-signed continuation tokens use version 2, binding the independently verified Cognito owner (via keyed pseudonym), table, exact page limit, search and 15-minute expiry. Other-owner replay, changed limit/filter, expired/empty/forged/legacy cursor tokens fail before any further DynamoDB scan. Only the server selects the current owner from the verified session, never a browser query. Invalid or missing legacy monthly allowance is shown as **Unavailable** rather than manufactured zero, and the browser suppresses cap changes when the stored allowance is unknown. No authoritative tenant data is altered by these read-only projections.

## Owner-only persistent audit receipt inspection (opt-in)

When both `EnableAdminConsole=1` and `EnableAdminMutations=1` are explicitly enabled, successful tenant suspension/reactivation, tenant rule-action changes and downward request-cap changes atomically persist an immutable audit event alongside the tenant revision in the dedicated retained, deletion-protected audit table. A verified owner can now inspect one event by its 64-character ID using the authenticated /admin Audit Receipt panel or `GET /v1/admin/audit/{event_id}`. Each lookup uses a single, strongly consistent DynamoDB GetItem and an explicit allowlist projection; it is **not** a Scan or log enumeration. The same verified Cognito owner and recent MFA authorization is enforced server-side as other /admin APIs. A missing ID produces 404, invalid ID 422 and malformed row/provider outage 503. Only validated actor, tenant, UTC time, redacted before/after, revisions and reason are returned. No API keys, billing bindings, raw tenant settings, export, deletion, or Stripe operations are available. IAM adds only audit-table GetItem to existing conditional PutItem without allocating more capacity. This feature is a receipt inspection workflow, not a complete searchable audit ledger, and is unavailable without the opt-in.

## Verified subscription reconciliation triage (read-only)

The authenticated owner **Verify Stripe** workflow now returns and displays a redacted reconciliation assessment from the same direct Stripe snapshot and authoritative saved tenant record. It marks a saved active Stripe entitlement with an inactive/unverified provider binding as `critical`; an active provider whose saved tenant entitlement is inactive, billing mode conflicts, or tenant account is disabled as `review`; matching provider/saved states as `consistent`; and unbound inactive records as `unavailable`. Every result retains its source and UTC read timestamp only when a real provider read occurred. The UI provides next-step guidance, never simulates AWS costs or invoice amounts, never grants paid access, and never writes Stripe, billing or tenant state. Provider errors remain 503. The existing audited tenant suspension is a separate operator action. Persistent read audit and signed-MFA production activation remain pending.
