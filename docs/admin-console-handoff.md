# Owner admin console implementation handoff

Updated November 23, 2026 (America/New_York). This is the concise continuation
record for a new chat. The append-only engineering history remains in
[director-agent-coordination.md](director-agent-coordination.md); the complete
47-target product discussion is preserved in [product-roadmap.md](product-roadmap.md).

## Start here in a new session

1. Fetch current `main` and read the coordination file's top override, then
   `AGENTS.md`. Check the actual main SHA, every exact-SHA CI job, open issues and
   active writers before changing files. This handoff is evidence of prior work,
   not a substitute for those fresh checks.
2. Continue requirements [#39](https://github.com/CollectiverseStudio/guardoath/issues/39),
   [#40](https://github.com/CollectiverseStudio/guardoath/issues/40),
   [#41](https://github.com/CollectiverseStudio/guardoath/issues/41) and
   [#42](https://github.com/CollectiverseStudio/guardoath/issues/42). Work directly
   on main with one writer. Read-only parallel reviews are useful; concurrent
   implementation writers are not.
3. Fix a failing exact-SHA job first using its actual failing test/log and root
   cause. Preserve assertions, the original 500-ms PII deadline, customer Off
   choices, shared tenant authority, audit, cost controls and rollback.
4. Keep Greg updated as meaningful work and verification progress. Update this
   handoff with substantive implementation commits so a new chat can continue.
   Do not create status-only commits or repeatedly suggest manual preflight.

The latest authorization is owner-only Cognito/MFA administration. Older issue
text proposing billing/support/publisher roles does not authorize exposing
delegated privileges. Current server enforcement deliberately permits only owner.

## Latest concrete batch

The introducing commit of this handoff builds on
`af911757950f52f7dbf43789d619ab1e6cb45538`. Use
`git log -1 --format=fuller -- docs/admin-console-handoff.md` to locate its exact
commit; never infer acceptance from a nearby SHA.

### Owner subscription reconciliation and durable request caps

- `guardoath/admin_subscription_reconcile.py` implements the actual owner API
  action and the existing `/admin` UI exposes it. The service reads a trusted
  Stripe snapshot, applies only the configured plan to the bound tenant, and
  commits the tenant change and signed audit receipt atomically. It cannot
  change a Stripe subscription, issue a refund, or mark an invoice paid.
- Tenant updates compare both the original saved JSON and revision. The same
  request key recovers its original signed receipt after a lost response;
  changed request contents cannot reuse that key. Browser retries retain that
  exact request, verify the receipt, and ignore responses from signed-out
  sessions. A successful verified action remains visible if a later refresh
  fails.
- `admin_monthly_allowance_cap` and `stripe_plan_monthly_allowance` are protected
  tenant fields, outside customer-editable settings. Both memory and DynamoDB
  adapters use the same cap-aware reconciliation. Saved caps, including zero,
  survive delinquency and recovery; suspension, keys, usage and customer checks
  remain unchanged. Absent provenance fields are omitted from stored JSON.
- A bound legacy subscription without provenance conservatively preserves its
  saved allowance as a cap. **Legacy zero remains zero.** This batch has no
  cap-raising or cap-release workflow; do not claim every legacy reactivation
  can restore the nominal plan automatically.
- Active admission requires the configured price/environment, one item of
  quantity one, automatic collection and explicitly unpaused collection.
  Read-only inspection can still show manual/paused subscriptions. Known
  nonactive states can still remove entitlement when quantity has changed.
- Customer and owner allowance parsing now agree at the supported maximum of
  1,000,000,000 requests. The number is a request budget, not financial spend.

### Real application acceptance coverage

- `tests/admin_browser_app.py` mounts the actual application, auth exchange,
  signed JWT validation, fresh owner/MFA verifier, admin routes, authoritative
  DynamoDB adapter and signed audit reader. Only the external Cognito/Stripe
  boundaries and typed database transport are synthetic. Its database persists
  on disk and is independently reread from another process.
- `tests/browser/admin_integration.spec.js` exercises reconciliation, request
  caps, suspension, Cognito account enable/disable, signed catalog draft/review,
  and denied customer/non-MFA/stale-MFA/forged-token access. There is no HTTP
  response interception in this suite.
- `tests/browser/admin_subscription_reconciliation.spec.js` adds focused browser
  fault tests for lost responses, corrupt outcomes/receipts, definitive rejected
  requests, failed refresh and late signed-out responses. These use synthetic
  HTTP boundaries and complement the mounted-application suite.
- The Tests dashboard job runs both suites; Stripe acceptance includes the new
  backend reconciliation cases. No existing assertion or deadline was removed.

### Executable rollback compatibility gate

- `guardoath/state_contracts.py` declares supported and introduced persistent
  state contracts. The Lambda builder puts the declaration and exact source SHA
  in the release manifest.
- `deploy/aws/verify_rollback.py` inspects bounded ZIP manifests without executing
  or extracting code. It checks the candidate source SHA, the downloaded
  backup's AWS `CodeSha256`, preserved contract support and whether that backup
  can understand every contract the candidate introduces.
- The actual `Deploy-Verified.ps1` calls the gate before changing Lambda and
  rechecks the backup digest before restoration. Release acceptance now requires
  all seven workflows / 25 individual jobs, including Stripe and deployment
  script acceptance.
- **This source introduces cap provenance. It must not deploy over a legacy
  rollback image. A compatible baseline must first be implemented, tested,
  deployed and accepted. That baseline is not implemented or deployed in this
  batch.** Do not remove the gate, strip saved caps, or claim schema compatibility
  from a manifest declaration alone. See the operator guide for the contract.

## Verification actually obtained before publication

The implementation and roadmap were published at
[`53399dd8058d79e1bb6953632192d5a0e14eeee4`](https://github.com/CollectiverseStudio/guardoath/commit/53399dd8058d79e1bb6953632192d5a0e14eeee4).
Its actual owner browser/real API acceptance step passed in dashboard job
138148102847, and both Stripe acceptance jobs passed. Deployment-script job
138148102564 failed because the last deliberately rejected native Python test
left PowerShell's process exit code at one, despite all assertions completing.
The follow-up source corrects only that harness success exit after assertions
and cleanup. All negative cases and the production gate remain intact. The
follow-up commit still needs its own full exact-SHA CI; this predecessor's
partial success is not release acceptance.

| Evidence | Result and limits |
| --- | --- |
| Baseline `af911757950f52f7dbf43789d619ab1e6cb45538` | All seven workflows / 25 jobs passed; this does not accept the new batch |
| Python 3.13 broad admin, Stripe, tenant/store, release and rollback modules | 1,514 passed; the existing opt-in real-DynamoDB test could not run without a real table and AWS credentials; no new skip introduced |
| Final customer-allowance/runtime regressions | 47 passed after the maximum-allowance parity correction |
| Python 3.12 selected browser-shell, rollback, tenant and reconciliation modules | 258 passed |
| Actual mounted ASGI application smoke | Owner auth 200; customer, missing MFA, stale MFA and forged token 403; authenticated features/overview/users/tenants/catalog 200; tenant reconciliation revision 8 and signed receipt verified |
| JavaScript source/spec syntax and diff whitespace | Passed |
| Local Playwright | Server starts; Chromium launch is blocked by OS socket permission before test assertions execute. No local browser pass claimed |
| Actual PowerShell execution | Unavailable locally; new acceptance script is wired into exact-SHA CI |
| New commit CI | Pending when this handoff was written; inspect exact-SHA job results before release |
| AWS / live Stripe / deployed `/admin` | No live mutation or deployment in this batch; no live financial or end-to-end readiness claim |

Offline test transport is not AWS acceptance. The existing real-table test was
not converted to a fake pass, weakened or removed. This environment had no
authenticated AWS deployment identity; an installed SDK alone does not provide
one. No AWS resource or paid provider was purchased or provisioned.

## Remaining work and completion boundaries

1. Obtain the new commit's exact-SHA CI results, including actual Chrome and
   PowerShell execution. Diagnose and repair any failure before further release
   work. Do not count review or syntax checks as browser acceptance.
2. Implement the compatible rollback baseline and establish real source parity,
   verified backup and all 15 live acceptance results when authenticated release
   access is available. Issue #30 remains an unresolved disclosed latency risk.
3. Complete the real financial management operations required by #41, including
   configured plan changes/cancellation and auditable, idempotent provider
   reconciliation. Current reconciliation is an entitlement refresh; it is not
   an upgrade, charge, cancellation or refund API.
4. Complete verified infrastructure cost ingestion, freshness/currency and
   defensible tenant attribution. Request counts and request caps are not AWS
   billing data or tenant profitability. Do not fill missing money values with
   zero or claim a live financial dashboard from synthetic tests. The overview
   currently returns unavailable metrics; actual failure counts, 500-ms tail
   events, margins and health evidence still need trustworthy sources.
5. Complete #42's versioned category/check/rule publishing and rollback through
   the shared runtime. Existing signed draft/review/manifests and offline source
   application are not proof that an owner can safely publish a deployed catalog
   change end to end. Preserve stable identifiers and all saved tenant Off states.
   Current authoring is metadata-only; complete schema/example inspection and
   actual executed-test receipts before claiming general rule authoring.
6. Review remaining user-management and authentication acceptance directly
   against #39–#42 and the operator guide. Existing Cognito account operations
   require actual signed audit, authorization and deployed MFA evidence.
7. Keep all four issues open until their actual requirements are verified. Then
   progress the preserved product roadmap in its recorded priority order. Stop
   recurring implementation only after the console is implemented, verified and
   deployed.

## Useful source and evidence locations

- [Operator guide](admin-console-operator.md): current feature boundaries,
  authorization, audit, reconciliation and rollback contracts.
- [Tenant record guide](tenant-records.md): shared source of truth and protected
  billing provenance.
- [Production live acceptance](production-live-acceptance.md): authoritative
  LIVE-01 through LIVE-15 table. Record passed, failed, blocked or not run against
  the full source SHA and configuration revision.
- `tests/test_admin_subscription_reconcile.py`: provider/state/receipt corruption,
  replay, durable reopen, atomicity, race, zero-cap and customer-policy coverage.
- `tests/test_rollback_compatibility.py` and
  `tests/powershell/rollback_contract.ps1`: real ZIP and real deployment-function
  acceptance without AWS calls.
- `.github/workflows/tests.yml`, `stripe-reconciliation-acceptance.yml` and
  `deploy-script-acceptance.yml`: new coverage is integrated with existing gates.
- GitHub Actions exact-source listing:
  `GET /repos/CollectiverseStudio/guardoath/actions/runs?head_sha=<40-character-SHA>&per_page=100`,
  then inspect each run's individual jobs. A PR-only workflow lookup or empty
  commit status is not a CI result.
