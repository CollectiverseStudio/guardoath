# GuardOath website on AWS (guardoath.com)

One CloudFormation stack, `deploy/aws/guardoath-site.json`, in **us-east-1**. It is designed to cost
nothing: every part is either covered by the CloudFront flat-rate **Free** plan or stays inside an AWS
always-free allowance, and alarms stop the site before Lambda's free allowances run out. The cost table
below says where each figure comes from.

```
visitor --HTTPS--> CloudFront (Free plan) --> AWS WAF (1 rate rule, in the plan)
                        |  origin access control (SigV4)
                        v
                 Lambda function URL (AWS_IAM) --> guardoath-site (python3.13, 512 MB, 5 at a time)
                                                        |
                                                        v
                                               DynamoDB guardoath-tenants (provisioned 10/10)
alarms (invocations and run time, each per 24 hours) --> guardoath-site-shutoff (sets the site's concurrency to 0)
```

- **Caching:** `/assets/*` (page script, styles, logo, video parts) is cached at CloudFront. Everything
  else (the page, the API) is not cached and reaches the app with all viewer headers except Host.
- **Keys:** CloudFront replaces `Authorization` with its own signature, so the hosted API takes the key in
  `X-GuardOath-Key`. A POST must also send `x-amz-content-sha256` (hex SHA-256 of the body). The dashboard
  does both. See `docs/tenant-records.md`.
- **Video:** served in parts of at most 4 MiB, because a Lambda function URL response is limited to 6 MB
  (4 MiB is 5,592,408 characters in base64). The demo video (3,580,895 B) fits in one part, so it is always
  sent whole.
- **Demo users:** created in the table on the first request; their keys come from the `DemoKeySecret`
  parameter (kept in the function's environment).

- **Requests at the same time:** the function runs `SiteConcurrency` requests at once (5 by default). A
  request over that is refused by Lambda with HTTP 429 before the app runs (nothing is counted), and the
  dashboard sends it again after a short wait (up to 4 times). With 2 at a time, one page load was already
  too many, so parts of the page (such as the demo user list) failed at random. Reserved concurrency has no
  charge; at the highest setting (10) an hour of full load is 18,000 GB-seconds, under 5% of the free
  400,000 a month, and the run-time alarm (evaluated every minute) stops the site first.

## Cost table (all figures checked against AWS pricing pages or the AWS price list before deploying)

| Part | What it is | Cost | Source |
|---|---|---|---|
| CloudFront distribution, WAF web ACL + 1 rule + requests, Route 53 zone + records + queries | Flat-rate **Free** plan (`AWS::PricingPlanManager::Subscription`, tier FREE) | $0. Allowance 1M requests / 100 GB a month; above it AWS may slow the site but never charges | CloudFront flat-rate plan docs ("Costs covered by your plan"); CFN resource reference (free subscriptions activate immediately) |
| Lambda requests and compute | Always-free: 1,000,000 requests and 400,000 GB-seconds a month, shared across the organization | $0 while under the allowance; the alarms stop the site first (30,000 invocations or 24,000 s of run time at 0.5 GB in any 24 hours; 31 such days stay under both allowances) | AWS price list for Lambda (`_cost-price-lambda.json`); October organization usage: no Lambda |
| Lambda function URL | HTTPS endpoint for the function | No extra charge | Lambda function URL pricing docs |
| Data from Lambda to CloudFront | Origin fetches | $0 (AWS origin to CloudFront) | CloudFront pricing |
| DynamoDB table | Provisioned 10 read / 10 write units, under 1 GB | $0: the first 25 read and 25 write units (18,600 unit-hours each a month) and 25 GB are free | AWS price list for DynamoDB (`_cost-price-ddb.json`); October organization usage: no DynamoDB |
| CloudWatch alarms | 2 standard alarms on AWS/Lambda metrics | $0: 10 alarms are free; the organization used 2.45 in October | Free-tier usage report (`_cost-freetier-usage.json`) |
| CloudWatch Logs | 2 log groups, 7-day retention | $0 under 5 GB ingested a month (organization: 0.17 GB in October) | Same report |
| Custom metrics | None created (WAF metrics are off) | $0. The organization's 10 free custom metrics are already used, so the stack adds none | Same report |
| ACM certificate | guardoath.com + www | $0 (public certificates) | ACM pricing |
| CloudFormation, IAM, origin access control | | $0 | CloudFormation pricing (AWS::* resources), IAM |
| CloudFront invalidations | `/assets/*` after a code change (one path) | $0: the first 1,000 paths a month are free per account | CloudFront docs, "Pay for file invalidation" |
| Lambda code storage | ~40 MB zip, uploaded directly (no S3 bucket) | $0: the Lambda price list has no code-storage charge | `run_python_output_999.txt` (price-list usage types) |
| Cognito user pool (tenant sign-in) | Lite plan, native accounts created by an administrator; Google federation and separate GuardOath registration are opt-in (review provider and Cognito pricing before activation) | $0 up to 10,000 monthly active users per account or organization (the Lite free tier does not expire); then $0.0055 per user up to 100,000 | Cognito pricing page, "Free Tier" and the Lite example (checked 2026-11-02) |
| Cognito email (invitations, password reset) | Cognito's built-in email (`COGNITO_DEFAULT`), limited to a small number of emails a day | **Not verified:** the pricing page says email is sent through SES and points to SES pricing, but does not say whether the built-in option is charged. Only an administrator creates users, so there is one invitation per customer user | Cognito pricing page; Cognito developer guide, "Email settings" |
| Session checks | Each session's signature is checked locally with the pool's public keys (fetched at most once every 5 minutes). Only a correctly signed token reaches Cognito (GetUser): at most once a minute per session, at most 60 calls a minute and 4 at a time per copy of the function | No per-request charge is listed for user-pool sign-in calls; the outbound bytes are a few KB per call (**data transfer price not re-checked**) | Cognito pricing page |
| Already paid | Domain $16/year; hosted zone $0.50/month until it is attached to the Free plan | unchanged / then $0 | Route 53 billing |

**Small amounts that can happen once:**
- The web ACL exists for a few minutes before the Free plan covers it. WAF pay-as-you-go is $5 per web
  ACL-month plus $1 per rule-month, charged by the hour: about $0.008 per hour.
- If the Free plan subscription is refused (for example, AWS treats the account as "using AWS Free
  Tier", which makes it ineligible), CloudFormation rolls the whole stack back. The web ACL then exists
  for the time the rollback takes, at the same hourly rate.

## Before deploying

```cmd
cd /d C:\Users\bellgj\Documents\Personal\collectiverse\guardoath
aws lambda get-account-settings --region us-east-1 --profile collectiverse-prod > .guardoath-reports\lambda-account.json
type .guardoath-reports\lambda-account.json
```
Expect `"ConcurrentExecutions": 1000` and `"UnreservedConcurrentExecutions"` of at least 110 (Lambda keeps
100 unreserved; the site reserves `SiteConcurrency`, 5 by default, at most 10).

## Deploy

1. Build the package (needs internet; about a minute). It must run with Python 3.13 (the Lambda
   runtime): it compiles every module to bytecode Lambda can load as is, so a new copy of the function
   does not compile them again before it can answer. Expect a "Compiled ... modules" line, then the
   unzipped size under 262,144,000 bytes and the zip under 52,428,800 bytes:
   ```cmd
   cd /d C:\Users\bellgj\Documents\Personal\collectiverse\guardoath
   C:\Users\bellgj\AppData\Local\Programs\Python\Python313\python.exe deploy\aws\build_lambda.py
   ```
2. Make the demo key secret once and keep it (`.guardoath-reports` is not in git). Changing it later
   changes every demo key:
   ```cmd
   C:\Users\bellgj\AppData\Local\Programs\Python\Python313\python.exe -c "import secrets; print(secrets.token_urlsafe(48))" > .guardoath-reports\demo-key-secret.txt
   set /p GUARDOATH_SECRET=<.guardoath-reports\demo-key-secret.txt
   ```
3. Create the stack (10 to 30 minutes: the certificate is validated through DNS and the distribution is
   deployed worldwide). Expect `Successfully created/updated stack - guardoath-site`:
   ```cmd
   aws cloudformation deploy --region us-east-1 --profile collectiverse-prod --stack-name guardoath-site --template-file deploy\aws\guardoath-site.json --capabilities CAPABILITY_IAM --parameter-overrides DemoKeySecret=%GUARDOATH_SECRET% --no-fail-on-empty-changeset > .guardoath-reports\deploy-stack.txt 2>&1
   type .guardoath-reports\deploy-stack.txt
   ```
4. Upload the app (the stack starts with a placeholder that answers "being deployed"). Expect
   `Successful` and the code size:
   ```cmd
   aws lambda update-function-code --region us-east-1 --profile collectiverse-prod --function-name guardoath-site --zip-file fileb://deploy/aws/build/guardoath-lambda.zip --query "CodeSize" --output text
   aws lambda wait function-updated --region us-east-1 --profile collectiverse-prod --function-name guardoath-site
   aws lambda get-function --region us-east-1 --profile collectiverse-prod --function-name guardoath-site --query "Configuration.LastUpdateStatus" --output text
   ```
5. Check the site. Expect `ACTIVE` for the Free plan and `{"status":"ok",...}` from the site:
   ```cmd
   aws cloudformation describe-stacks --region us-east-1 --profile collectiverse-prod --stack-name guardoath-site --query "Stacks[0].Outputs" --output table > .guardoath-reports\stack-outputs.txt
   type .guardoath-reports\stack-outputs.txt
   curl -s https://guardoath.com/healthz
   ```

## If the first create fails

CloudFormation rolls the new stack back to `ROLLBACK_COMPLETE` and deletes what it created, except the
table `guardoath-tenants` (DeletionPolicy Retain and deletion protection, so tenant data is never lost
by a stack operation). The kept table makes the next create fail ("already exists"), and a stack in
`ROLLBACK_COMPLETE` must be deleted before it can be created again. After a failed **first** create the
table is empty (the app never ran), so:

1. Check that the table has no items (expect `0`):
   ```cmd
   aws dynamodb scan --region us-east-1 --profile collectiverse-prod --table-name guardoath-tenants --select COUNT --consistent-read --query Count --output text
   ```
2. Only if it is `0`: turn off deletion protection, delete the table, delete the failed stack, then deploy again:
   ```cmd
   aws dynamodb update-table --region us-east-1 --profile collectiverse-prod --table-name guardoath-tenants --no-deletion-protection-enabled
   aws dynamodb delete-table --region us-east-1 --profile collectiverse-prod --table-name guardoath-tenants
   aws dynamodb wait table-not-exists --region us-east-1 --profile collectiverse-prod --table-name guardoath-tenants
   aws cloudformation delete-stack --region us-east-1 --profile collectiverse-prod --stack-name guardoath-site
   aws cloudformation wait stack-delete-complete --region us-east-1 --profile collectiverse-prod --stack-name guardoath-site
   ```

While it exists the kept table costs $0 (10/10 provisioned units, inside the 25/25 always-free units).
If the table has items, do not delete it: the stack was live before, and the data must be kept.

## Customer Google sign-in and registration (explicitly opt in)

The previous deployed template created a Cognito app client with **only** `COGNITO` enabled, while
the dashboard always tried `identity_provider=Google`. Its Lambda configuration also omitted
`GUARDOATH_SELF_REGISTRATION`, leaving self-registration disabled. Those are deployment
configuration blockers, not defects that a green Python test or a GitHub push can solve.

The current template supports a Google identity provider through explicit, validated parameters:
`EnableGoogleLogin` (default `0`), `GoogleOAuthClientId` (default empty),
`GoogleOAuthClientSecret` (a `NoEcho` value, default empty), and
`EnableSelfRegistration` (default `0`). When Google is enabled, the provider is
created and the public Cognito app client enables `Google` alongside `COGNITO`.
The Lambda receives **flags only**; neither Google credential is put in Lambda
environment variables, logged, or stored in the repository. The registration flag
may not be enabled without enabling Google login, and an enabled provider cannot
be deployed with missing Google credentials.

### External Google OAuth configuration

Create an **OAuth 2.0 Web application client** under Google Auth Platform.
Configure its Google OAuth consent and testing/publishing status to permit the
accounts intended to register. Use this **Google authorized redirect URI**
(the *Cognito* callback, not the GuardOath dashboard callback):

`https://<SignInDomainPrefix>.auth.us-east-1.amazoncognito.com/oauth2/idpresponse`

The Cognito app client's own code+PKCE callback and logout URI are both
`https://guardoath.com/dashboard`. The server checks the ID provider's verified
identity, requires a verified email to derive the first-time tenant identity,
and `POST /v1/register` persists the tenant. No email/password, signed token,
client secret, tenant ID or payment claim is provided by the browser to create
an entitlement.

In the existing CloudFormation stack parameters, supply the actual Google Web
client ID and secret securely (do **not** paste them in a commit, ticket or shell
history); set `EnableGoogleLogin=1`. After verifying Google redirects and
reviewing the applicable Cognito charges, set `EnableSelfRegistration=1`.
A new tenant has status `inactive`, allowance `0`, and no API keys. A
registration retry after temporary HTTP 503 retains the verified browser session,
so the customer can retry without purchasing or logging in again.

**Verify the actual deployed stack, not the template alone:** inspect the
Cognito app client for Google support and the Google IdP's verified-email
mapping, the Lambda's two enablement flags, and the public `GET /v1/sign-in`
configuration. Then complete a real browser roundtrip using a Google account:
first sign-in, register, inactive status, denied paid check and API-key creation,
sign out, sign back in, unchanged tenant identity and settings. Repeat with a
second Google account to verify isolation. If you cannot complete these steps,
**do not claim Google registration is operational**. Paid plan activation and
Stripe webhook reconciliation are separate obligations (Issue #37).

The template defaults to **disabled** Google registration until the external
credential/configuration and cost review are finished; it never presents a
functional Google sign-in button on a deployment that only has the native
Cognito provider. Existing native, admin-created users remain supported on
the user pool; this browser provides the Google path only.

### Legacy native Cognito account administration

If you intentionally still provision a native Cognito user, an administrator
must create the user and set its immutable tenant attribute. This is separate
from Google self-registration and is not the new-customer journey. Native
accounts are not automatically paid or eligible to issue API keys.

## Prove whether the latest GitHub source is actually available on guardoath.com

GitHub `main` is **not** the hosted application. The live site uses the last
Lambda zip uploaded with `aws lambda update-function-code`; CloudFront may also
retain stale `/assets/*` files until an invalidation. Do not claim deployment
from a push, passing previous-SHA CI, a successful build, or a 200 health response.

The Lambda builder now **requires a clean committed app** and stamps its full Git
commit SHA in the package as `guardoath/_release_source.json`. The public
`/healthz` response includes `source_sha`; local development without that
package metadata returns `null`, not an invented release. A healthy response is
not proof of tenant API behavior, reference attestation or the 15 live checks.

From the **repository root** on your Windows PC, after fetching the desired
`main` commit, run this read-only verifier:

```powershell
git fetch origin main
git pull --ff-only origin main
git rev-parse HEAD
python -m tools.verify_live_dashboard --url https://guardoath.com
```

It compares every dashboard-linked versioned JavaScript/CSS file's SHA-256
suffix **and complete response bytes** against the local checkout. It also
compares `/healthz`'s embedded backend source SHA with that checkout's full
Git HEAD. **Exit 0** and `release_source_parity_verified=true` mean both
source checks passed; **exit 2** means stale/missing UI, backend SHA mismatch,
unreachable origin, missing build provenance, or incomplete verification.
The JSON output contains safe asset names and SHAs only, no credentials,
tenant IDs or uploaded content. The optional `--expected-sha` can pin an
explicit 40-character Git commit hash. The verifier does not use AWS
credentials or modify tenants or deployments.

**Important:** parity with a *stale local checkout* is not parity with
latest GitHub `main`. Always fetch and fast-forward before checking. Even
a full UI+backend SHA match does not assert behavioral acceptance:
require the full 13 individual Tests jobs, three CodeQL analyses,
specialized reliability suites at the exact release SHA, tenant/API/browser
live smoke tests, the saved-Off provenance audit, rollback readiness and
all 15 documented post-deployment checks. Historical 500-ms PII latency
failures must be resolved; do not stretch timeouts to force a green release.

## After a code change

Run steps 1 and 4, then clear the cached assets (one invalidation path):
```cmd
aws cloudfront create-invalidation --profile collectiverse-prod --distribution-id <DistributionId from the outputs> --paths "/assets/*"
```

## If an alarm stopped the site

The shutoff sets the site's reserved concurrency to 0, so every request gets HTTP 429
`ReservedFunctionConcurrentInvocationLimitExceeded`. Find out why first (alarm history, shutoff log,
invocations and run time per 5 minutes). Then turn the site back on **and** set both alarms back to OK:

```cmd
aws lambda put-function-concurrency --region us-east-1 --profile collectiverse-prod --function-name guardoath-site --reserved-concurrent-executions 5
aws cloudwatch set-alarm-state --region us-east-1 --profile collectiverse-prod --alarm-name guardoath-site-invocations-per-day --state-value OK --state-reason "Site turned back on by hand"
aws cloudwatch set-alarm-state --region us-east-1 --profile collectiverse-prod --alarm-name guardoath-site-duration-per-day --state-value OK --state-reason "Site turned back on by hand"
```

An alarm acts only when its state changes. A 24-hour alarm can stay in ALARM for up to a day after it
fires; if the site were turned back on during that time, the alarm would not act again. Setting it to OK
makes CloudWatch evaluate it again at the next minute: if the last 24 hours are still over the limit, it
goes back to ALARM and stops the site again.

**History:** on 2026-10-30 an hourly alarm (1,300 invocations an hour) stopped the site at 22:02 UTC after
about 1,400 requests in 40 minutes. The hourly alarms were replaced with these 24-hour ones.

## Removing the site

`aws cloudformation delete-stack --region us-east-1 --profile collectiverse-prod --stack-name guardoath-site`.
The table `guardoath-tenants` is kept (deletion protection and Retain); delete it separately if wanted.
