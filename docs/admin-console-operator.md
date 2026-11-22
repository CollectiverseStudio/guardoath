# GuardOath operator administration

The new `/admin` route redirects to `/admin/login` only when the admin feature is enabled. It is **disabled by default**. Ordinary customer API keys and Cognito sessions never grant owner privileges.

## Authorization
Set `GUARDOATH_ADMIN_ENABLED=1` only after establishing a separate public Cognito admin OAuth client with PKCE and callback `https://guardoath.com/admin/login`, in the existing trusted pool, and verifying independently fetched `GuardOathOwners` membership and signed current-login MFA evidence. Required: `GUARDOATH_ADMIN_CLIENT_ID`, `GUARDOATH_ADMIN_COGNITO_DOMAIN`, `GUARDOATH_ADMIN_CURSOR_HMAC_KEY` (independent 32–64 random bytes, hex). An invalid/missing configuration fails closed at startup. Each authorization request independently checks a complete Cognito group page: malformed, duplicate, oversized or paginated memberships (including an empty continuation token) deny owner access even if an owner group appears in the response. Distinct additional groups remain permitted. This changes no roles or enrollment. CloudFront forwards `X-GuardOath-Admin-Session`; `Authorization` is reserved for AWS origin signing.

## Capabilities
Read-only overview, bounded tenant directory and current-month usage, category/check/rule catalog, and on-demand provider-verified subscription lookup when existing `GUARDOATH_STRIPE_SECRET_KEY` is configured. Stripe snapshots do not change entitlement or expose payment details. Costs and revenue are unavailable without a separately approved verified data source.

Optional `GUARDOATH_ADMIN_AUDIT_TABLE` enables owner tenant suspension/reactivation, single tenant rule action updates, and downward-only allowance caps with enforced Block overage. **Do not enable** until a separate append-only DynamoDB audit table, least-privilege transaction IAM and operational restore procedure exist. Every mutation requires typed tenant confirmation, reason, expected revision and atomic update + audit. No billing changes, refunds or versioned global catalog publication are implemented yet.

## Release gate
Run full repo and browser tests plus exact-SHA Tests (13), CodeQL (3), Field Comparisons Evidence (2), Trusted Reference Acceptance (1), PII Reliability Gate (3); maintain 500-ms checker limit and the existing verified deploy script rollback and 15 live acceptance checks. Unresolved issue #30 must be reported, never silently closed. A GitHub commit is not an AWS deployment.

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
- Authenticated owner/MFA `GET /v1/admin/subscriptions/{tenant_id}/invoices` persists a signed protected read attempt before any tenant or provider read. It independently verifies the saved Stripe subscription/customer, configured price and environment before reading at most five provider invoices from server-owned IDs.
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
An independently verified Stripe invoice page now includes `payment_review` with counts of **open invoices with remaining balances** and **uncollectible invoices**, plus a currency-separated sum of remaining open balances. The owner UI cross-checks the summary against redacted invoice rows and fails closed on mismatched counts or amounts. A page is limited to five invoices; `complete_history=false` and a partial-history warning are shown if Stripe reports more results. These are bounded operator-review signals **not** verified payment failures, aging, account-wide receivables, collected revenue, refunds, entitlement status or AWS costs. No new Stripe calls, financial writes, credentials, provider spending or tenant-policy changes are introduced.


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
The existing guarded owner Catalog page lists published category/check/rule metadata, eligible Input/Output stages, supported actions, check use cases and limitations, plus counts of unit-test and evaluation files **declared** by the published registry. The API does not return filenames, detector paths, positive-example content or test fixtures. Counts never attest that current-SHA CI ran, that acceptance passed, or that the system was deployed; missing declarations are unavailable, not zero. Catalog reads now reject malformed or duplicate IDs, incomplete rule objects, inconsistent declared rule counts, and ambiguous stages/actions before returning partial results, including in filtered views. This inventory does not publish, roll back, or modify system rules or tenant-saved Off policies. Versioned publishing, live AWS cost reporting and 15/15 acceptance remain open.

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
