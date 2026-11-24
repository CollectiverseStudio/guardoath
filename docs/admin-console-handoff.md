# Owner admin console implementation handoff

Updated November 23, 2026 (America/New_York). This is the concise continuation
record for a new chat. The append-only engineering history remains in
[director-agent-coordination.md](director-agent-coordination.md); the complete
47-target product discussion is preserved in [product-roadmap.md](product-roadmap.md).

## Current implementation batch: protected invoice browsing

The existing **Tenants → Invoices** workflow now offers status filtering,
**Older invoices** and **Restart invoice history**. Each click performs one
five-row Stripe invoice read after fresh owner/MFA authorization, signed
persistent read intent, authoritative tenant lookup and independent subscription
verification. The production runtime wires the real existing provider adapter.
No provider mutation or tenant state field is introduced.

Continuation tokens encrypt provider invoice IDs with AES-GCM and are bound to
owner, tenant/customer/subscription, configured plan/environment, status filter
and page size. Their original 15-minute expiry does not slide. Invalid, changed,
cross-owner and expired selections fail before provider reads. A best-effort
per-warm-instance budget permits 20 page requests per owner per minute; it is not
a distributed limiter. Empty-more pages, repeated current anchors, newer/
out-of-order records and inconsistent bindings/filter/amounts fail closed.
Equal creation timestamps remain valid. This is not a frozen or globally
deduplicated financial snapshot.

Balances stay in integer minor units, separated by currency and limited to the
shown page. Filtered or continuation pages never claim complete invoice history,
including the last page. Sign-out, refresh, subscription inspection and confirmed
reconciliation clear prior invoice selections and amounts. Another tenant can
replace a pending selection; duplicate same-tenant activation cannot issue a
second in-flight read. The shared request helper also prevents an old session's
late 401/403 from signing out a newly authenticated owner.

Local verification: **1,268 Python 3.12 tests passed**, **218 focused Python 3.13
tests passed**, with no new skip or deadline change. The actual mounted ASGI app
returned five then three redacted invoices, one status-filtered invoice, three
independently verified signed read receipts and unchanged tenant data. Customer,
owner without MFA, stale-MFA and forged signed-token attempts were denied before
any invoice-provider boundary call. Filter/cursor mismatch returned 400 before
another provider read. External boundaries were synthetic; this is not live
Stripe or Cognito evidence.

The real baseline owner browser suite now includes pagination/filter/audit and
four denied-login cases; the default suite adds 33 fault, race, native-reauthentication,
single-flight and mobile cases. JavaScript parsing/discovery is separate from
browser execution. This invoice batch still needs its own exact-source CI;
local Chromium remains unavailable under the workspace's socket restrictions.

The complete financial-operation continuation decisions are preserved in
[admin-financial-operations-design.md](admin-financial-operations-design.md):
scheduled cancellation preview, exact confirmation, durable target serialization,
single dispatch, independent verification and ambiguity-safe recovery. That
document is a design, not a completed cancellation/refund/credit API. The original
47-target roadmap remains intact.

## Accepted release safety batch: rollback revision ownership

The deployment script now binds automatic rollback to the candidate digest and
the revision returned by **its own successful update response**. It cannot take
a newly observed revision after a rejected or lost upload and use it to overwrite
another deployment. A current hash/revision mismatch, uncertain original update
receipt or in-progress update refuses restoration. A post-read race is still
protected by the original revision condition. A restored result requires a fresh
digest/revision/success-state verification; already-present backup bytes are only
an observation, and lost restore responses are not blindly retried.

The actual production functions passed every scenario in the new PowerShell
ownership suite. All **six PowerShell steps from the actual deployment-acceptance
workflow passed locally**, including existing ZIP compatibility, parse/guard,
individual-job and issue-risk checks. The original negative archive tests now
require their specific refusal reason. Python archive/build/template checks:
**65 passed**. PowerShell 7.4.6 was obtained from the official release and its
archive verified against the official checksum; no AWS/provider call occurred.
Published source `2602cb50cdfe1b23163530e29192307c9027e3e7` passed all seven
required workflows / 25 individually inspected jobs. Tests
[45312551709](https://github.com/CollectiverseStudio/guardoath/actions/runs/45312551709)
passed 13 jobs; CodeQL 45312551652 (3), Field Comparisons 45312551677 (2), Trusted
Reference 45312551811 (1), PII 45312551787 (3), Stripe 45312551799 (2) and Deploy
Script [45312551797](https://github.com/CollectiverseStudio/guardoath/actions/runs/45312551797)
(1) all passed. No failed, cancelled, missing or skipped job. Actual dashboard
job 138171592996 passed 4 baseline owner, 7 writer owner, 19 invocation and 462
default browser tests. Deployment acceptance executed all six PowerShell steps,
including actual revision-ownership functions, plus 28 Python archive cases.
This accepts that source's CI, not the later invoice batch or a live rollback.

A real clean Python 3.13 Lambda build for **2602cb5 only** produced 48,714,031 ZIP
bytes / 130,346,420 unzipped bytes, digest
`NCqstKbEEWXuF6wyvkaI3TpB+RdOFVwxPo+luJxrKms=`. Its manifest names the exact SHA,
supports the cap contract and introduces none. Packaged source-phase bytes match;
no test harness is included. The archive was not uploaded and is not parity
evidence for later source or production.

## Catalog inspection implementation

The existing owner Catalog page now has **Inspect check** and **Inspect rule**
actions backed by exact-target, owner/MFA-authenticated API reads. The same
registry provides descriptions, examples, prerequisites and evidence paths;
the real tenant model provides only the selected check's configuration schema
and reachable definitions. No tenant record is read or changed. The persistent
signed read-attempt audit precedes source access.

Examples are escaped text, including invisible and directional characters.
Source/schema/example bounds, exact version/identity checks, account-field
exclusion, selection/refresh/sign-out race guards and literal DOM rendering
protect inspection. The old counts-only inventory and its draft/history
fingerprints are unchanged. `rule_payload.settings` now exposes the existing
source's example prerequisites; it does not change detector behavior.

Python 3.12 admin/catalog/source-history/real-rule-example regression:
**1,580 passed**. Actual client-validator smoke accepted check/rule and the
maximum Unicode fixture and refused five injected account fields. New fault
browser cases and a mounted-application inspection journey are integrated into
the existing CI suites. Catalog source `276a1871` passed all eight triggered
workflows / 26 jobs, including 4 baseline owner, 7 writer owner, 19 invocation
and 462 default browser tests. Source declarations shown inside the inspector
are not executed-CI or live-deployment attestations.
Authenticated publication/rollback and general definition authoring remain open.
Python 3.13 focused catalog/API/browser-shell/source-history run: **315 passed**.

## Current release phase: implemented compatibility baseline

The current source supports `subscription-cap-provenance-v1` while introducing
none of that state. `ENABLE_NEW_SUBSCRIPTION_STATE=False` in
`guardoath/state_contracts.py` governs both artifact declarations and actual
production writers. There is no environment or build-argument override.

Both stores preserve and enforce existing caps and plan provenance, including
zero and later writer receipts. Legacy customer activation, positive request
caps, existing delinquency downgrade, user/tenant controls and catalog review
remain available without adding new fields. Full owner reconciliation is retained
and tested but is not mounted in this baseline. New provenance-bearing records
and new full-reconciliation receipts are refused before a write/provider call.

The first-activation regression is also fixed: a positive blocking allowance
retains its limit even before a Stripe subscription is bound. An unbound zero-cap
mutation without provenance is specifically refused in the baseline, with a
clear instruction to use the existing Suspend action. This prevents a new zero
cap from becoming indistinguishable from registration. Historical unbound zero
records cannot prove which history occurred; do not claim their provenance was
recovered. Legacy bound zero remains conservative during recovery.

**Do not enable the writer source until this actual baseline is deployed and
accepted as the compatible rollback.** A later source commit can then enable
creation; its artifact will automatically declare the contract introduced and
the gate will require that verified compatible backup. No baseline deployment
or live acceptance is established in this workspace.

Original baseline local evidence: **1,555 Python 3.12 tests passed** in
the broad admin/Stripe/tenant/release run, with the existing opt-in real-AWS test
unavailable without credentials; **210 Python 3.13 targeted tests passed** after
the unbound-cap correction. Actual signed-owner ASGI smoke saved a 4,000-request
legacy-shaped cap, left full reconciliation unmounted (404), and isolated an
inherited Cognito redirect setting. The repaired baseline subsequently passed
all required exact-SHA CI at `1c9d8851`, including actual Chrome, as recorded below.

### Predecessor failures and accepted correction

Baseline commit
[`d6998c70d34db2af575c5843e5976db052c01325`](https://github.com/CollectiverseStudio/guardoath/commit/d6998c70d34db2af575c5843e5976db052c01325)
is **not release accepted**. All nine workflows / 28 jobs were inspected:
25 succeeded, two failed and one was cancelled. The eight specialized workflows
succeeded, including all three PII reliability jobs. Both failures are in
[Tests run 45307985727](https://github.com/CollectiverseStudio/guardoath/actions/runs/45307985727):

- Dashboard job [138156392308](https://github.com/CollectiverseStudio/guardoath/actions/runs/45307985727/job/138156392308):
  `admin_baseline.spec.js:91` received the intended API 409 for an ambiguous
  unbound zero cap, but the UI displayed only `Request failed (409)` instead of
  the supported Suspend guidance. The shared request helper intentionally hides
  arbitrary server error bodies. The follow-up adds an operation-scoped,
  allowlisted error header and a fixed client explanation. Four additional
  browser cases cover recognized, unknown, absent and wrong-status error signals
  and ensure private response bodies never render. The original real-application
  test and backend refusal remain unchanged. The other two baseline browser
  cases passed; later browser steps were not executed after the failure.
- Python 3.12 job [138156392746](https://github.com/CollectiverseStudio/guardoath/actions/runs/45307985727/job/138156392746):
  `test_uc033_extracted_receipt_fields_only[record1-block-output]` expected a
  complete schema result but received incomplete. The log omitted checker error
  and latency, so **the root cause is unproven**; do not label it a timeout or
  scheduler defect. The unchanged 24-case module passed locally. Its complete
  assertions now include only checker ID/status/error code/latency diagnostics,
  preserving every assertion and the original 250-ms schema budget. No test
  isolation, retry, skip or clock/deadline change is introduced. Python 3.13 was
  cancelled by matrix fail-fast and cannot count as successful acceptance.

The cap explanation/API change passed **129 focused Python 3.13 tests**, actual
client/spec syntax checks and a read-only privacy review. The full local Python
3.12 suite then passed **22,200 tests**, with the single existing opt-in AWS test
unavailable. All 25 required CI jobs passed at `1c9d8851`. The receipt failure did
not recur, but its original root cause remains unproven; no runtime latency fix,
deadline change or issue #30 resolution is claimed.

A real Python 3.13 Lambda build was also produced for **d6998c7 only**:
48,694,800 ZIP bytes, 130,288,585 unzipped bytes, code digest
`L/lcCtNX/I0EbAZ6BNs4thX9dt/Ko/ME74pHyk5NAVs=`. Its manifest names that exact SHA,
supports the cap contract and introduces none; source-controlled phase bytes
match and no test harness is packaged. The archive was not uploaded. It is not
source-parity evidence for this later follow-up or for production.

### Accepted catalog predecessor: exact CI evidence

All **26 individual jobs** at
[`276a187191cc326d1b1106849b11345df0d7a519`](https://github.com/CollectiverseStudio/guardoath/commit/276a187191cc326d1b1106849b11345df0d7a519)
were individually inspected and passed, with no missing, failed or cancelled
job: the seven required workflows / 25 jobs plus the extra red-team job. This
accepts the catalog source's CI, not this later rollback-ownership batch or a
production deployment.

| Exact-SHA workflow | Jobs | Evidence |
| --- | ---: | --- |
| Tests | 13 | [45310982995](https://github.com/CollectiverseStudio/guardoath/actions/runs/45310982995) |
| CodeQL / Push on main | 3 | [45310983347](https://github.com/CollectiverseStudio/guardoath/actions/runs/45310983347) |
| Field Comparisons Evidence | 2 | [45310982947](https://github.com/CollectiverseStudio/guardoath/actions/runs/45310982947) |
| Trusted Reference Acceptance | 1 | [45310982959](https://github.com/CollectiverseStudio/guardoath/actions/runs/45310982959) |
| PII Reliability Gate | 3 | [45310983071](https://github.com/CollectiverseStudio/guardoath/actions/runs/45310983071) |
| Stripe Reconciliation Acceptance | 2 | [45310982956](https://github.com/CollectiverseStudio/guardoath/actions/runs/45310982956) |
| Deploy Script Acceptance | 1 | [45310983080](https://github.com/CollectiverseStudio/guardoath/actions/runs/45310983080) |
| Red-team regression evidence | 1 | [45310982950](https://github.com/CollectiverseStudio/guardoath/actions/runs/45310982950) |

Dashboard job 138166353168 executed **4 baseline owner, 7 retained writer owner,
19 guarded-invocation and 462 default browser tests**, all passed. The original
zero-cap explanation and actual catalog-inspection journey passed. Earlier
`1c9d8851` and writer source `e17baba1` also passed all 25 required jobs; their
detailed evidence remains in coordination history. These are offline external-service boundaries;
live Cognito/AWS/Stripe behavior and all 15 live checks remain unverified.

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

## Preserved full owner reconciliation implementation

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
- The original writer introduced cap provenance and correctly refused a legacy
  rollback. The current source now implements the actual reader/enforcement
  baseline described above. It must be deployed and accepted before enabling the
  later writer. Do not remove the gate, strip saved caps, or claim compatibility
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
follow-up `e17baba1` subsequently passed all 25 required jobs, as recorded above.
This paragraph preserves the original failure history; the latest baseline and
follow-up require their own acceptance.

| Evidence | Result and limits |
| --- | --- |
| Baseline `af911757950f52f7dbf43789d619ab1e6cb45538` | All seven workflows / 25 jobs passed; this does not accept the new batch |
| Python 3.13 broad admin, Stripe, tenant/store, release and rollback modules | 1,514 passed; the existing opt-in real-DynamoDB test could not run without a real table and AWS credentials; no new skip introduced |
| Final customer-allowance/runtime regressions | 47 passed after the maximum-allowance parity correction |
| Python 3.12 selected browser-shell, rollback, tenant and reconciliation modules | 258 passed |
| Actual mounted ASGI application smoke | Owner auth 200; customer, missing MFA, stale MFA and forged token 403; authenticated features/overview/users/tenants/catalog 200; tenant reconciliation revision 8 and signed receipt verified |
| JavaScript source/spec syntax and diff whitespace | Passed |
| Local Playwright | Server starts; Chromium launch is blocked by OS socket permission before test assertions execute. No local browser pass claimed |
| Actual PowerShell execution | The later rollback-ownership batch executed all six real workflow steps locally; all passed with offline AWS boundaries |
| Original writer follow-up CI | `e17baba1` passed all 25 jobs; see the separate latest-baseline failure record above |
| AWS / live Stripe / deployed `/admin` | No live mutation or deployment in this batch; no live financial or end-to-end readiness claim |

Offline test transport is not AWS acceptance. The existing real-table test was
not converted to a fake pass, weakened or removed. This environment had no
authenticated AWS deployment identity; an installed SDK alone does not provide
one. No AWS resource or paid provider was purchased or provisioned.

## Remaining work and completion boundaries

1. Obtain the new commit's exact-SHA CI results, including actual Chrome and
   PowerShell execution. Diagnose and repair any failure before further release
   work. Do not count review or syntax checks as browser acceptance.
2. Deploy and accept the implemented compatible rollback baseline, establish real source parity,
   verified backup and all 15 live acceptance results when authenticated release
   access is available. Issue #30 remains an unresolved disclosed latency risk.
3. Complete the real financial management operations required by #41, including
   configured plan changes/cancellation and auditable, idempotent provider
   reconciliation. Current reconciliation is an entitlement refresh; it is not
   an upgrade, charge, cancellation or refund API. Continue from the preserved
   [financial-operation design](admin-financial-operations-design.md); implement
   durable provider-target control rather than relying only on a browser action
   key or Stripe's expiring idempotency cache.
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
   Current authoring is metadata-only. The new schema/example inspector has
   exact-SHA browser acceptance at `276a1871`; actual executed-test receipts
   attached to publication and behavior authoring remain unfinished.
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
  `tests/powershell/rollback_contract.ps1`, `rollback_ownership.ps1`: real ZIP,
  real deployment-function and revision-ownership acceptance without AWS calls.
  Local PowerShell, when this workspace remains available, is
  `.guardoath-reports/release-runtime/powershell-7.4.6/pwsh`; the workflow itself
  remains the durable execution recipe.
- `.github/workflows/tests.yml`, `stripe-reconciliation-acceptance.yml` and
  `deploy-script-acceptance.yml`: new coverage is integrated with existing gates.
- GitHub Actions exact-source listing:
  `GET /repos/CollectiverseStudio/guardoath/actions/runs?head_sha=<40-character-SHA>&per_page=100`,
  then inspect each run's individual jobs. A PR-only workflow lookup or empty
  commit status is not a CI result.
