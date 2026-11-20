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
