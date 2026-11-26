# Owner admin console implementation handoff

Updated November 25, 2026 (America/New_York; November 26 UTC). This is the concise continuation
record for a new chat. The append-only engineering history remains in
[director-agent-coordination.md](director-agent-coordination.md); the complete
47-target product discussion is preserved in [product-roadmap.md](product-roadmap.md).

## Current implementation: owner financial controls activated in source

The next coherent batch enables `ENABLE_NEW_SUBSCRIPTION_STATE` and
`ENABLE_NEW_CANCELLATION_STATE`; both catalog flags remain true. It provides
full owner entitlement reconciliation with durable request-cap provenance and
reviewed period-end cancellation, using the already deployed 762e7b6 compatible
reader as the release baseline. The generated artifact introduces all four
supported state contracts. This source batch still requires its own exact-source
CI and guarded deployment; the completed production release below remains the
last established live source.

The Billing page identifies whether reconciliation, request caps, cancellation
and historical recovery are available, including missing signed audit, Stripe
reader, plan allowance or dedicated cancellation setup. Only nonsecret booleans
reach the browser. Actual mounted services, owner/MFA authority, same origin and
signed audit remain authoritative. Reconciliation must share the real tenant
client/table and its exact audit client/table/signing key. Canonical actor
validation now precedes all reconciliation I/O, matching its signed reader.

Cancellation displays pending provider confirmation, provider-confirmed
historical success, ambiguous provider outcome and recovered signed history
distinctly. Unknown durable claims remain held and never trigger a new POST.
Confirmed scheduling preserves current tenant access, caps, keys, policies and
usage. Refunds, credits, provider plan changes, cap restoration and durable
lifecycle/risk processing remain unfinished; this does not complete CORE-04.

Focused local acceptance passed 203 backend/runtime cases, 216 source/rollback
contract cases and 158 UI/API/mounted cases. These are separate, potentially
overlapping groups, not a summed full-suite count. The three new fresh-process
financial scenarios exercise explicit disabled compatibility, untouched-source
confirmed scheduling and a lost provider reply. They verify actual signed owner
JWT/MFA/origin/audit denials, authoritative provider bindings, exact confirmation,
lost reconciliation-response recovery, durable 4,000-request caps and independent
signed-state reopen with exactly one synthetic provider POST. Runtime tests also
reopen financial history after removing the write credential.

The actual owner JavaScript and edited specifications parse. Browser discovery
finds 9 mounted source-default journeys, 11 explicit compatibility journeys and
55 cancellation/reconciliation fault cases. Local Chrome was not executed; its
required final-source CI remains authoritative. A bounded integration review
found no remaining source-default assumption or route-authority blocker.

The existing authorized AWS session is retained. A necessary read of the new
feature's runtime prerequisites timed out through both SDK and the existing CLI;
no authentication rejection was returned, no configuration was changed and no
new login was requested. Production prerequisite presence is therefore unknown,
not inferred from source activation. Do not discard the signed-in session or
repeat health audits while finishing this batch.

## Current production: confirmed catalog publication requests enabled

**Deployed source: `762e7b66be029f07d245d9d11dc8a08aed32fd67`.** The accepted
`Deploy-Verified.ps1 -Deploy` runner completed with exit 0 and AWS `Successful`.
The deployment evidence records `2026-11-26T00:22:35Z` (November 25, 7:22 p.m.
Eastern). All seven required workflows and **25 individual jobs** passed for
this exact source; the deployment verified the exact backend source and
**13/13 dashboard assets**.

The shared AWS access-portal sign-in succeeded. The AWS console browser returned
HTTP 403, but the standard CLI deployment succeeded using the already-authorized
temporary `AdministratorAccess` role credentials obtained through the portal's
**Access keys** flow. That browser error and the earlier device-authorization
timeout did not prevent this completed release.

| Recorded deployment evidence | Value |
| --- | --- |
| Deployed archive SHA-256 (base64) | `Jiuzcc81AQolQvmX9GB/ZSQfxwMzhokGP98UHGTr6fU=` |
| Actual preserved rollback source | `d81db4d9c418cd198fc8f1e48df42a5ca3822a5f` |
| Actual preserved rollback archive SHA-256 (base64) | `ZllvLTKAOGLgiUwszWJTKhi0jwakY0GSJ4aEWzpc73o=` |

Both catalog writer gates are now live; the subscription-cap and cancellation
writer gates remain false. Owner-confirmed requests support only an existing
rule's `title` and `use_case`. An operator must start the release worker; the
browser does not start a background service. **No live catalog write was
submitted during this deployment.**

`production_live_acceptance` remains **false**: these release results do not
establish all 15 live acceptance areas, resolve issue #30, finish broader owner
or financial administration, or supply general category/check/rule authoring.
Continue the unfinished roadmap work from this deployed baseline. Revisit this
deployment for a new change, an observed failure, or a specific new operation
requiring AWS access; do not reopen login or repeat a healthy-deployment audit
solely because a new chat starts. The hourly **GuardOath Deployment Readiness**
automation was paused at Greg's direction.

The implementation and earlier CI failures below are retained historical
records. Their pending-activation or sign-in statements are superseded by this
completed deployment.

## Historical implementation record: activate confirmed metadata publication

Greg continued after the completed lifecycle deployment. The new source enables
both `ENABLE_NEW_CATALOG_PUBLICATION_STATE` and
`ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE`; subscription-cap and financial
cancellation writers remain disabled. Its artifact introduces exactly the two
catalog contracts. The accepted compatible reader baseline is the already
deployed `d81db4d9c418cd198fc8f1e48df42a5ca3822a5f`, so the next release can preserve
a rollback image that understands every new catalog record.

The actual prior deployment completed with AWS `Successful`, archive digest
`ZllvLTKAOGLgiUwszWJTKhi0jwakY0GSJ4aEWzpc73o=`, exact backend/dashboard source and
all **13** dashboard assets. Its preserved backup was
`aa907ba94470c841c5509a8ecbe03f2fd620fac8`. All seven required workflows and **25**
individual jobs passed on d81. This is retained observed evidence; this activation
batch does not repeat a healthy-deployment audit or create a retroactive receipt.

The owner workflow requires a signed current review, fresh owner MFA, same-origin
request, explicit typed confirmation/reason and stable action reference. Supported
changes remain an existing rule's title/use-case description. Preview/history and
the browser explicitly identify `processing_mode=operator_started`: submitting a
request does not start a background service. An authorized operator runs the
bounded worker for at most one current request and can return after candidate CI.
The existing progress, unknown-outcome handling and signed completion remain
authoritative.

Operator setup now includes `--production-config`, which reads the fixed existing
AWS function through the operator's existing profile, validates its account and
admin configuration, and derives the existing audit signing key in memory. It
does not copy signing secrets into arguments, files or logs, or provision a new
table/role/scheduler. The guarded deployer uses `GH_TOKEN` or `GITHUB_TOKEN` when
provided for fixed-host GitHub evidence reads, refuses redirects, and reports
sanitized failures. Git push authorization is a separate existing prerequisite;
the GitHub connector does not export a CLI credential.

Focused state/source/rollback compatibility acceptance passed **293 tests** on
Python 3.13. The real source-default lifecycle includes verified completion, a
successor request and immutable earlier recovery. The actual build manifest
introduces both catalog contracts; a digest-bound d81-contract fixture passes,
while five unsupported/legacy backup variants fail. Explicit disabled-reader and
environment-override refusals remain tested.

UI/service acceptance passed **122 tests**, including three mounted phases:
explicit disabled compatibility, the financial fixture using actual catalog
defaults, and completely unchanged source gates. The last phase enables catalog
requests while financial writers remain false; customer, missing/stale MFA,
forged identity and wrong origin are denied before state/source access. The
activated mounted paths each verify 19 signed read receipts and one request
transaction through recovery. Browser discovery found **52 publication**, **9
mounted writer** and **11 compatibility** cases; actual Chrome execution belongs
to the new commit's CI because this restored workspace has no Chromium binary.

Worker acceptance passed **105 tests** across the two worker modules. The actual
PowerShell authenticated-reader test and all eight existing individual-job gate
scenarios passed. A fixed-ref non-mutating Git push dry run detects missing or
denied push setup before consuming the publication claim; only the acknowledged
claim permits the one actual push. Read-only integration review found no blocker.
These focused groups are not a summed full-suite count. The activation commit
still requires its own complete CI and deployment; local results do not establish
production activation, a live catalog mutation or all fifteen live areas.

### First activation CI failure and bounded test correction

Activation source `b09e48c062809c9959f630f40180759822231e88` was published but
is **not accepted or deployed**. Five required workflows succeeded. First-attempt
[Tests run 45650190032](https://github.com/CollectiverseStudio/guardoath/actions/runs/45650190032)
failed the same 17 new operator tests in local-pii (3.12) job `139332281980`,
test (3.11) job `139332282045`, and windows-launcher (3.12) job `139332282023`.
The CI dependency set is `.[test,pii]`; the local interpreter had additionally
installed `[aws]`. The tests imported optional `boto3`/`botocore` merely to
configure synthetic transports. Without those packages, positive bootstrap and
12 invalid-binding cases failed before their fake calls, and four CLI cases
raised `ModuleNotFoundError`. Linux reported 17 failed/22,781 passed/9 existing
skips; Windows reported 17 failed/22,849 passed/13 existing skips.

The correction supplies function-scoped explicit synthetic SDK modules for
those unit tests, preserves the real worker/state paths and every assertion,
and adds a missing-SDK refusal case. A separate interpreter uses the exact CI
dependency set and independently confirms neither optional SDK package is
installed. All **18** focused cases (the original 17 plus the explicit missing-SDK
refusal) passed in 1.26 seconds in that environment. Runtime code, release flags, dependencies, deadlines and existing
skips do not change. The replacement commit needs its own complete CI.

Separately, first-attempt [CodeQL run 45650189705](https://github.com/CollectiverseStudio/guardoath/actions/runs/45650189705)
failed Python job `139333123459`, JavaScript/TypeScript `139333123743` and Actions
`139333123745` at result upload after extraction and SARIF export. The logs have
no error/HTTP explanation; all three check summaries/text are null and annotations
are empty. No source defect or infrastructure root cause is established.
The failed-only and single-job retry tools each returned an internal error with
no success acknowledgement; the last observed attempt was still 1. Do not claim
that a retry succeeded or that the failed scan accepted this source.

The restored workspace did not retain the earlier temporary AWS credential files;
the shared AWS portal currently presents its sign-in screen. Greg's existing
authorization for the GuardOath release session and assigned production role
continues to apply. A fresh sign-in/session is needed before the next deployment;
no new permission scope is being requested. Preserve the completed d81 baseline
until the activation candidate passes its own release gates.

## Previous implementation: complete publication lifecycle

Greg directed completion of the existing strategy and stopping repeated stability
checks. The strategy and all 47 original targets remain in
[product-roadmap.md](product-roadmap.md). Current work completes the next CORE-06
step after local source preparation: fixed-repository remote publication, exact
candidate CI, guarded deployment evidence, terminal control release and owner
progress/history. It does not broaden supported edits beyond existing-rule
`title`/`use_case` or claim general category/detector authoring. Implementation
and focused acceptance are integrated as one source batch.

The new signed lifecycle contract records acknowledged publication/deployment
claims and immutable remote, exact-source CI, deployment and terminal receipts.
A verified terminal chain allows the next owner request to atomically replace
global control without deleting earlier evidence. Earlier action references
remain recoverable. The next proposal must match the completed source/version/
metadata; stale deployment or browser state cannot authorize it.

The bounded external runner verifies the retained history-only candidate,
pushes once without force to the fixed repository, reads every required exact-
candidate CI job, and invokes that candidate's guarded deployment script with a
fresh operation-bound receipt. Its `--next` entry point selects at most the one
current signed request. Unknown external results keep their claims; no retry,
reset, table scan or background cloud scheduler is added. An operator must run
the worker. Normal completion consumes the deployer's fresh source/asset receipt
once; separate interrupted-stage recovery performs necessary new readback.

The owner UI now displays the complete stage chain and **Review another
release**, which independently rereads terminal/control eligibility before a
fresh draft/review/confirmation. All four persistent-state writer flags remain
False in this compatibility source. Catalog writes require both catalog gates,
and activation needs an accepted deployment/rollback image supporting
`catalog-publication-lifecycle-v1`. Broad rule publishing and general detector/
category/check creation are not claimed.

Focused evidence at integration: **253** state/provenance/rollback compatibility
tests and **121** service/runtime/mounted/browser-shell tests passed. The actual
client validator accepted all nine service stages and rejected seven corrupted
receipts. Playwright discovery includes **50** focused publication cases, with
20 added here; actual Chrome execution remains part of this new commit's CI.
The deployment evidence validator passed **32** cases, and the real PowerShell
rollback/acknowledged-completion functions passed, including asynchronous revision
changes, conflicting updates, changed configuration and private-input handling.
The release worker exercised **57 distinct cases** in focused runs (38 initial,
15 added, and 14 final affected cases with overlap). These include real local Git,
compiler/app/Engine and reopened signed-state paths; remote GitHub/AWS/deployment
transports are synthetic. Missing local deployment prerequisites are refused
before consuming a deployment claim. All implementation owners have frozen their
files. These groups overlap and are not a summed suite count. Full new-source CI
and candidate deployment are distinct from the completed `aa907ba` baseline below.

### First lifecycle CI attempt and exact fixture correction

The full lifecycle batch was published at
`529343c0c6e65cf953843c59fe6f3645d038410c`. Its first Stripe acceptance attempt
([run45540836797](https://github.com/CollectiverseStudio/guardoath/actions/runs/45540836797),
job138958665362) failed one exact manifest expectation in
`test_subscription_compatibility_baseline.py`: it still listed three supported
contracts instead of the four now declared. The environment-disable assertion
passed; the job reported **1failed,590passed,1existing skip**. This candidate is
not accepted or deployed. Deploy, PII, signed reference and CodeQL workflows
succeeded; the remaining workflows were pending when monitoring that obsolete
SHA stopped. No same-source rerun was dispatched.

The correction adds `catalog-publication-lifecycle-v1` to that exact expected
list. All **36** subscription-baseline tests then passed locally. The other two
exact supported-contract expectations already include the new contract. No
application behavior, writer flag, assertion requirement, privacy deadline or
skip changed. The replacement source requires its own complete CI before
deployment.

### Completed production baseline

`aa907ba94470c841c5509a8ecbe03f2fd620fac8` passed all **25 required individual
jobs** across all seven workflows, including both Windows suites and real Chrome.
Greg explicitly approved the GuardOath release session's temporary AWS credentials
and use of existing assigned roles. AWS production account `839000214942` and
its assigned `AdministratorAccess` role were verified. The earlier unanswered
consent and browser-console 403 notes below are historical, not current blockers.

That exact baseline is now running on `https://guardoath.com`. Lambda reported
`Successful` with `CodeSha256=2RpJ7NymtmKbrkn1e0FGpdWBkKrine1cSXuropTYsmk=`;
health reported the exact source SHA. Full dashboard HTML and all **13** linked
assets matched the checkout with no parity issues. Assets were invalidated. The
previous code archive and configuration were preserved privately; the archive
matched its original AWS digest. No tenant, billing, IAM, environment, concurrency
or writer configuration was changed.

The old deployment runner stopped after upload because it assumed the update
response and completed function must have the same revision. The actual
acknowledged update was `InProgress`; AWS assigned a different revision on
completion while preserving the exact uploaded code. The original response was
corroborated by its CloudTrail event, and the independent live source/asset result
completed this code rollout. No second upload or automatic rollback occurred.
The new runner integration binds acknowledged timestamp, code and unchanged
configuration, retains the original upload revision separately, and never adopts
a completion observation as rollback authority. Its new operation-bound receipt
is for future catalog publications; no retroactive machine receipt is invented
for the baseline.

This establishes LIVE-01 and the source/asset component of LIVE-02, plus a
preserved compatible backup. It does not establish all fifteen live areas,
current native owner MFA, customer onboarding/billing, paid model inference or an
exercised rollback. Those journeys were not repeated in this continuation at
Greg's direction. The unresolved #30 latency risk and original 500-ms fail-closed
deadline remain unchanged.

## Historical batch: owner publication request and local source preparation

The owner Catalog now connects a verified signed review/manifest to a separate
publication preview, retained explicit request writer and read-only history/
action-reference recovery. The service regenerates the entire current source
manifest/history, accepts authoritative source SHA only from the strict packaged
release marker, and requires typed `PUBLISH` or `REVERSE` plus review suffix,
reason and stable reference. Actual owner/fresh-MFA, same-origin and signed
read-audit checks precede state/source access. The browser validates exact
review/target/source/metadata bindings, independently rereads the signed intent,
retains single-flight recovery across refresh and suppresses late logout results.

The independent `catalog-publication-v1` contract atomically reserves one global
catalog control and immutable intent. An external exact-ID worker verifies the
signed review, exact clean base and repository origin, runs the existing
compiler and real source/app/Engine acceptance in a detached worktree, and
retains a local candidate commit with signed preparation evidence. It does not
push, dispatch CI or deploy. Its standalone audit-key-file CLI requires POSIX
permission verification; the injected-state callable is portable.

**The production source remains compatibility-only:**
`ENABLE_NEW_CATALOG_PUBLICATION_STATE=False`; the request route is unmounted,
and service/state/worker writes independently refuse. Broad `rule_publishing`
is false. Supported edits are only existing-rule `title`/`use_case`. This batch
can record and prepare one candidate under tests. It has no normal worker
scheduling, remote publication, complete CI/deployment receipts or terminal
control release. Global control stays occupied indefinitely even after
`SOURCE_PREPARED`; no expiry, reset or automatic retry is implemented. Keep the
writer disabled until compatible deployment **and** the remaining lifecycle are
both verified. A deployed baseline alone does not complete this workflow.

Local evidence: the broad admin/catalog/rollback run passed **1,624 tests**.
The final consolidated focused suite passed **259 tests**, including all
25 worker cases, state/provenance/API, both mounted journeys, both compatibility
contracts and rollback. The two fresh-process mounted ASGI journeys deny customer, missing-MFA,
stale-MFA and forged signed identities before state/source reads. Origin and
audit failures also block access. Reopened storage independently verifies seven
baseline and eleven writer read receipts, one writer intent/transaction, exact
replay/recovery without current source and unchanged catalog/tenant records.
All external transports and the harness source marker are synthetic.

The implementation is committed on `main` at
`240355368c70bd8eb69c62bed3ce03e345dcf664`. Its actual CI Chrome job
[138908279800](https://github.com/CollectiverseStudio/guardoath/actions/runs/45525682989/job/138908279800)
passed **11** mounted baseline journeys, **9** retained-writer journeys,
**19** invocation journeys and **567** default browser cases, including the
**30** new publication cases. Local Chrome remains unavailable because of
browser download/socket restrictions; the browser execution evidence is CI.
No assertion or deadline was relaxed. The full contract and limits are in
[the catalog publication record](admin-catalog-publication-design.md) and
[the operator guide](admin-console-operator.md#owner-catalog-request-review-and-preparation-history-2026-11-25).

**First-attempt release gate: 24 of 25 jobs passed across seven workflows.**
The Tests workflow's Windows Python 3.12
[job 138908279713](https://github.com/CollectiverseStudio/guardoath/actions/runs/45525682989/job/138908279713)
failed the existing `test_redaction_still_masks_the_whole_value` bank-account
case: **1 failed, 22,718 passed, 12 existing skips**. The initial PII finding
took 2.133 ms, but the final evaluation was incomplete/Block with redaction
withheld and 532.51 ms total latency. This is consistent with the unresolved
#30 second-pass latency risk; truncated recheck diagnostics do not establish
the exact error or scheduler/GC cause. Windows Python 3.13 and every other job
passed. The deployment-script job passed all six actual workflow steps,
including 29 archive tests; installed-wheel acceptance matched all 228 rules
and 239 required assets. **Do not describe this first attempt as accepted.**

The bounded unchanged-application recheck at
`db3de2b81ca8f01b03dd754361c1327ba48dbd8a` failed a different existing case:
Linux Python 3.12
[job 138915521216](https://github.com/CollectiverseStudio/guardoath/actions/runs/45527851353/job/138915521216),
`test_uc068_declared_coordinate_count_and_shape[coords2-False-input]` in
`tests/test_catalog_partial_boundaries.py`. Its complete diagnostics identify
`schema / error / checker_timeout / 427.958 ms` against Policy's original
**250-ms schema budget**. The sweep reported 1 failed, 22,685 passed and nine
existing skips; Linux Python 3.11/3.13 were cancelled, not passed. Both Windows
jobs passed, including the existing isolated 36-case catalog module. This
source also lacks complete release acceptance.

That schema failure appeared at 18% of the large collection. Process state,
GC and host descheduling remain hypotheses, not established causes. The first
layout follow-up, `c6b01ba1d52d94da130db29849f224c02fc1ffef`, passed all 36
boundary cases in a fresh process in **all eight Linux jobs**. Its remaining
Linux Python 3.12 suite nevertheless failed the sole sibling module that
imports the same helper:
[job 138919464822](https://github.com/CollectiverseStudio/guardoath/actions/runs/45529038882/job/138919464822),
`tests/test_catalog_partial_contracts.py::test_uc092_overposting_and_missing_fields[payload5-False-input]`.
Diagnostics were `schema / error / checker_timeout / 565.376 ms`; the sweep
reported 1 failed, 22,649 passed and nine existing skips. Linux Python
3.11/3.14/3.15 were cancelled. Windows Python 3.12
[job 138919464610](https://github.com/CollectiverseStudio/guardoath/actions/runs/45529038882/job/138919464610)
also failed two cases in that same sibling module, reporting schema timeouts
at 422.437 ms and 419.23 ms (2 failed, 22,717 passed, 12 existing skips).
The full run ended with **20 successful jobs, two failures and three
cancellations**. Actual Chrome again passed 11/9/19/567 tests. This third
candidate is also unaccepted; no same-source automatic retry was made.

The CI group now includes the complete shared-helper family: **36 boundary
cases plus 58 contract cases, 94 total**, run together in each existing fresh
catalog process in Linux `test`, Linux `local-pii` and Windows. Only those
two modules are excluded from the later sweeps, retaining one execution of
every case. The actual combined local command passed all 94 cases in 5.49
seconds. A structural workflow comparison verifies unchanged other commands,
jobs, matrices and failure gates. No application, detector, Engine, model,
test, assertion, timeout, skip or automatic retry changed. This completes the
test-group process boundary; it does not establish a fix for underlying
latency. Both the 250-ms schema and 500-ms privacy budgets still reject late
results.

Inspect all seven workflows and all 25 jobs for the current main before any
deployment; neither prior failed attempt nor an older green source can accept
it. A passing CI cycle does not close #30. Preserve and investigate any further
failure rather than automatically repeating runs or weakening expectations.

The clean Python 3.13 package for `240355368c70bd8eb69c62bed3ce03e345dcf664`
was built and checked against all **655** tracked application/policy files.
It is 48,840,667 bytes compressed and 130,716,082 bytes unpacked, with
`CodeSha256=q20IzC8dTVOZjJeBM+WP35wQeKESVc27Mo/VfBDsgdA=`. Its manifest supports
all three state contracts, introduces none, and contains no test harness.
It is not an accepted/deployed release. Rebuild for the exact accepted current
main after the documentation update; do not deploy an older package.

AWS handoff: Greg's portal sign-in was verified and production access was
visible, but the console/federation pages returned HTTP 403 in this environment.
The official device authorization flow reached the new
**GuardOath release session** application-consent page. Automatic approval
review rejected token issuance and local token persistence because explicit
consent for that application to use existing assigned AWS roles remained
unanswered. Do not retry that credential acquisition or use another path until
Greg explicitly approves that scope. Public SSO client registration occurred;
no role credentials were obtained and no production changes occurred.
Live health still identified
`a9154dabbeff945bb19b901e20cde34b17b63fe7`; `/admin/login` returned 200 and
anonymous admin feature access returned 401. These observations are neither
candidate deployment parity nor LIVE-14 native owner MFA acceptance. The 15
live acceptance areas and compatible rollback remain required.

The complete original 47-target roadmap and the remaining #39–#42 production
acceptance requirements are preserved.

## Predecessor: consistent catalog loading and real source reversal

Catalog startup and the existing release CLI now share the same strict JSON
decoder and source-size constant. Six regression cases failed before the fix:
runtime accepted duplicate last-key-wins history and read the whole file before
enforcing its limit. Startup now bounds its actual read to 256 KiB plus one,
rejects duplicates at every depth, nonfinite/overflowed numbers and malformed
UTF-8, and retains valid finite JSON/Unicode. No detector, shipped catalog
content, tenant record, subscription writer flag or PII deadline changes.

The new acceptance uses the entire real registry in an isolated application
copy. Actual authenticated draft/review/manifest routes and independently
reopened signed evidence feed the real CLI, which writes a candidate and then
a freshly reviewed inverse at another higher version. Three fresh app starts
verify customer `/v1/controls`, owner `/v1/admin/catalog`, all rule dataclasses,
complete history and byte-for-byte persistent tenant preservation. Twelve real
Engine cases at each stage prove both-stage positive/benign behavior, Input rule
Off and Input parent Off overriding a saved rule Block. Candidate/restored
boots cannot silently recreate missing tenants. No catalog provider or source
file path is monkeypatched; the original checkout history remains unchanged.

Local evidence: **1,303 Python 3.12 tests passed** across catalog/admin/real-rule
examples; **57 Python 3.13 source/runtime tests passed**; the final persisted-row
guard round trip passed again on Python 3.12. Identity-provider and database
transports are synthetic. This proves source/app/API/Engine behavior, not a
customer login, Chrome journey or live catalog publication. Published predecessor
`1e4fba61ebf9ae15e7cf000c1e2533b63e8b0c49` subsequently passed all 25
individually inspected exact-source CI jobs. That acceptance does not cover the
current request/preparation batch.

The current request/preparation batch above extends that evidence while keeping
the existing source-shipped catalog as authority. Remaining release completion
and general category/check/rule authoring are recorded in
[admin-catalog-publication-design.md](admin-catalog-publication-design.md).

## Accepted cancellation compatibility release

The existing owner Billing workspace now integrates period-end cancellation
review, exact typed confirmation, reason, action reference and independently
verified historical recovery. The real API uses the existing Cognito owner/MFA
verifier, same-origin checks, authoritative tenant binding and signed read audit.
The current production source is a **support-only compatibility release**:
`ENABLE_NEW_CANCELLATION_STATE=False`. Review is available when its explicit
provider configuration exists; historical receipts work without a write
credential. The scheduling route is unmounted, and service, state and provider
write boundaries independently refuse dispatch. The retained writer is tested
through an isolated harness, not enabled by an environment variable.

The narrow provider adapter verifies its own configured Stripe account, mode,
exact saved customer/subscription and supported single-item active automatic
plan. It accepts only an explicitly unpaused, unscheduled subscription with no
pending update and a valid unchanged period. The 180-second encrypted preview
binds owner, tenant document/revision, provider fingerprint and prior control
document. A preview from before another completed operation cannot authorize
a later generation.

The future writer atomically reserves a stable provider target and inserts its
immutable signed intent. A separate transaction checks the exact saved tenant
document/revision while acquiring dispatch ownership. Only its successful
response permits one fixed `cancel_at_period_end=true` POST; account and
subscription are then independently reread before a signed verified outcome.
Two owners, tenant aliases or conflicting action references cannot acquire the
same dispatch. Lost replies and corrupt or unknown evidence retain the claim;
receipt recovery never repeats POST, expires the claim or invents success from
current provider state. Scheduling leaves the tenant document, usage, keys,
entitlement, request cap and saved customer Off settings unchanged.

The API derives a maximum eight-second admission/coordination budget from the
trusted Lambda remaining-time context, uses one-second provider boundaries and
checks available time before claiming and before POST. This is **not a hard
wall-time guarantee**: blocking database/body reads can still yield an
indeterminate retained operation. The Lambda template's 15-second timeout and
500-ms PII deadline are unchanged. Only `dynamodb:ConditionCheckItem` on the
existing tenant table is added to the existing conditional admin policy; no
resource or capacity is added.

Actual local evidence: **1,590 broad Python 3.12 tests passed**; **254 focused
Python 3.13 tests passed**, including final budget admission cases. Mounted ASGI
baseline and writer journeys denied customer, missing-MFA, stale-MFA and forged
signed identities before the provider boundary. Baseline review made two GETs
and direct scheduling returned 404; writer admission made one POST and six GETs
including preview, independently reopened three signed financial documents and
left both saved tenants unchanged. All external transports were synthetic.
JavaScript syntax and discovery passed: **42** new focused browser cases,
**10** total actual baseline journeys and **8** total actual writer journeys.
Discovery is not browser execution; local Chromium remains socket-blocked.
Published cancellation source
[`18127ec6214f49f1bd0d0947d775522e50841bbf`](https://github.com/CollectiverseStudio/guardoath/commit/18127ec6214f49f1bd0d0947d775522e50841bbf)
passed all **seven workflows / 25 individually inspected jobs**. Tests
[45320373489](https://github.com/CollectiverseStudio/guardoath/actions/runs/45320373489)
(13), CodeQL 45320372981 (3), Field Comparisons 45320373500 (2), Trusted Reference
45320373484 (1), PII 45320373442 (3), Stripe 45320373466 (2) and Deploy Script
45320373436 (1) all passed with exact job SHA and complete enumeration. Dashboard
job 138197193640's actual Chrome logs confirm **10 baseline owner, 8 writer owner,
19 invocation and 537 default browser tests passed**. Stripe backend job
138197193113 ran all four new cancellation modules: 591 passed, with the existing
opt-in real-DynamoDB test unavailable. Deployment acceptance included 29 Python
rollback cases and the actual PowerShell steps. No failed, missing, cancelled or
skipped job. This accepts cancellation-source CI, not the later catalog batch.

A clean Python 3.13 Lambda build for **18127ec only** contains all 651 tracked
application/policy files byte-for-byte: 48,773,398 ZIP bytes / 130,524,192 unzipped
bytes, CodeSha256 `1135tcRfkq3qQhhYClug3iDgLc8mSkU/O8ulnq8Sius=`. Its manifest
names the exact source, supports both contracts and introduces none; no test
harness is packaged. It was not uploaded and is not a later catalog-source or
production artifact. No AWS deployment, live financial data or live `/admin`
acceptance is established.

The implemented contract, configuration and recovery limits are recorded in
[the financial-operation design and implementation record](admin-financial-operations-design.md)
and [the operator guide](admin-console-operator.md#owner-period-end-cancellation-review-and-recovery-2026-11-24).
Refunds, credits, plan changes, lifecycle/risk event processing, real cost and
revenue ingestion, catalog publication/rollback and 15 live checks remain open.
The complete original 47-target roadmap remains intact.

## Accepted predecessor: protected invoice browsing

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

Published invoice source `ab65cd27cb59e41880d3b38c3205ef9a5beda201` passed all
seven exact-SHA workflows / **25 individually inspected jobs**: Tests
[45314905970](https://github.com/CollectiverseStudio/guardoath/actions/runs/45314905970)
(13), CodeQL 45314905215 (3), Field Comparisons 45314905953 (2), Trusted Reference
45314905962 (1), PII 45314905971 (3), Stripe 45314905922 (2) and Deploy Script
45314905968 (1). Actual dashboard job 138179286234 passed **9 baseline owner,
7 writer owner, 19 invocation and 495 default browser tests**. Stripe backend
job 138179285012 passed 422 tests with only the existing opt-in real-DynamoDB test
unavailable; both invoice modules actually executed. No failed, cancelled,
missing or skipped job. These results accept that invoice source, not this
later cancellation source or production.

A clean Python 3.13 Lambda artifact for **ab65cd2 only** contains all 648 tracked
application/policy files byte-for-byte, with exact source manifest and no test
harness: 48,724,051 ZIP bytes / 130,376,543 unzipped bytes, CodeSha256
`aXE/JKWbK7zLlogawmoPaAzHFPwTB8TZzDfbdMK9fMI=`. It supports cap provenance and
introduces none. It was not uploaded and is not a cancellation-source artifact
or live source-parity evidence.

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
The new full-registry source round trip and remaining publication bridge are
recorded in the current batch and the linked catalog publication design above.

## Current release phase: implemented compatibility baseline

The current source supports `subscription-cap-provenance-v1` and
`stripe-period-end-cancellation-v1` while introducing neither contract's state.
Independent `ENABLE_NEW_SUBSCRIPTION_STATE=False` and
`ENABLE_NEW_CANCELLATION_STATE=False` declarations in
`guardoath/state_contracts.py` govern both artifact introductions and actual
production writers. Neither enables the other. There is no environment or
build-argument override. Cancellation history has a real signed reader in this
baseline; ordinary protected reads reuse the prior audit resource vocabulary.

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

1. Complete CORE-04/#41 financial controls: finish this source activation's
   exact-source CI, guarded release and explicit runtime prerequisites, then
   implement refunds, credits, configured plan changes and durable lifecycle/risk
   event processing. The implemented scheduled-cancellation operation does not
   supply these capabilities; existing reconciliation is an entitlement refresh.
   Preserve the actual target/intent transaction and conservative ambiguity
   recovery in [the financial-operation record](admin-financial-operations-design.md).
2. Complete CORE-06/#42 authoring, versioned category/check/rule publishing and
   rollback through the shared runtime. Deliver usable owner controls for behavior
   and schema authoring beyond the current metadata-only scope, with executed-test
   receipts attached to publication. Preserve stable identifiers and saved tenant
   Off states, and finish the actual owner-request-to-deployment workflow.
3. Complete CORE-05 verified infrastructure cost ingestion, freshness/currency and
   defensible tenant attribution. Request counts and request caps are not AWS
   billing data or tenant profitability. Do not fill missing money values with
   zero or claim a live financial dashboard from synthetic tests. The overview
   currently returns unavailable metrics; actual failure counts, 500-ms tail
   events, margins and health evidence still need trustworthy sources.
4. Finish remaining user-management and authentication implementation required by
   #39–#42 and the operator guide, with actual signed audit, authorization and
   deployed MFA evidence for the completed Cognito account workflows.
5. Fix the concrete issue #30 latency defect without weakening the 500-ms deadline
   or fail-closed behavior. Its status remains unresolved.

Complete the outstanding LIVE-01 through LIVE-15 evidence alongside the relevant
implemented owner and customer workflows, recording each result against the
actual deployed source and configuration. Full production acceptance remains
false until all required areas pass. This is not a separate recurring general
audit: exact-source CI, deployment, compatible backup and backend/dashboard parity
for `762e7b6` are already recorded above. Revisit that completed evidence only for
a new change or concrete failure; every later release retains its required gates.
Keep #39–#42 open until their actual requirements are met, then continue the
preserved roadmap in its recorded priority order.

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
