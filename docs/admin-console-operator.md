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
