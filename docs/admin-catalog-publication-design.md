# Owner catalog publication: implementation evidence and completion boundary

Updated November 25, 2026 (UTC), for #42 and CORE-06 of the preserved
[product roadmap](product-roadmap.md). The current source implements owner review/history, separately confirmed
requests, external source preparation and a bounded release worker through
remote publication, exact-candidate CI, guarded deployment receipts and verified
terminal control. Both source-controlled catalog writer gates remain disabled
for the compatibility baseline. No live catalog edit, automatic background
worker service or general category/detector authoring is claimed.

## Existing authority and working operations

The shipped `catalog_metadata_releases.json` is the versioned description
history. `catalog_metadata.RELEASE_DATA`, `rule_catalog.RULES` and
`control_registry.REGISTRY_VERSION` load from that source; the customer app
builds `/v1/controls` at startup. Owner Catalog reads project the same registry.
The existing tenant record and shared Engine still decide which checks and
rules run, with the customer's per-stage Off choices preserved.

Owners can already inspect the real catalog, preview supported rule-description
changes, save signed drafts, record signed reviews and export a reproducible
forward/inverse source plan through the authenticated UI/API. The offline
`catalog_source_release` tool recomputes the complete projected metadata hashes,
checks the review/export shape and next version, and writes the tracked history
under an exclusive lock and byte comparison. `--revert-last` requires a freshly
reviewed exact inverse of the latest release and appends another higher version.
It does not delete history or silently lower the version.

These existing transformations support only `title` and `use_case` for an
existing rule. General category/check/rule authoring and detector/schema changes
are not implemented by that source tool. A signed review says `published=false`;
an exported hash is not a signature or an instruction to deploy.

## Predecessor acceptance: real source round trip

The new acceptance copies the complete application and policy assets into an
isolated directory. It then executes five fresh subprocess stages:

1. Boot the actual customer app and owner admin integration against the full
   shipped registry and existing typed DynamoDB adapter. Through the authenticated
   routes, save a signed draft, record its signed review and obtain the manifest
   for `agent_safety / lexical_guards / agent.git_force`. Reopen the persisted
   draft/review through a separate database connection and derive the existing
   browser export shape.
2. Run the real source-release CLI with that export, the actual default history
   file and actual published registry in the copied package. No provider or
   file-location monkeypatch substitutes a small catalog.
3. Start a fresh app from the candidate source. Verify the rule object, customer
   `/v1/controls`, owner `/v1/admin/catalog` and entire history fingerprint chain.
   Create a new signed draft/review/manifest for the exact inverse.
4. Run the actual CLI's `--revert-last` path, appending another version.
5. Start a third fresh app and verify restored descriptions, monotonic version,
   complete history, both API views and unchanged saved tenant records.

The test asserts that all imported catalog modules come from the isolated copy,
and that the original checkout's history bytes never change. Both full API
projections remain equal to their own baseline except for the intended title
and version. All real rule dataclasses match again after reversal.

Twelve real Engine cases execute at each of the three app stages. The selected
rule's actual positive and benign examples run on Input and Output for an
enabled tenant, a tenant with only Input rule Off, and a tenant with the Input
parent check Off despite a saved rule Block. Positive enabled cases block;
benign and disabled cases allow; parent Off prevents checker participation.
All results must be complete under the existing deadlines. Tenant JSON/revision,
usage, keys, subscription/allowance and attachment Off records remain unchanged.

The verified-session/Cognito and DynamoDB transports are synthetic. The test
does not contact AWS, execute Chrome, commit a catalog version, deploy a package
or establish live acceptance. It supplies a real source/app/Engine acceptance
layer alongside the existing browser and release gates.

## One strict interpretation at startup and source generation

Six new failures reproduced a concrete inconsistency before the fix: runtime
loading accepted duplicate root keys whose last value was valid, including
overwritten nonfinite values, and read the entire file before checking its size.
The source writer already rejected duplicate keys and bounded its read.

Both paths now use the same recursive strict JSON decoder and history-size
constant. Duplicate keys, nonfinite constants, overflowing numbers and malformed
UTF-8 are rejected. Startup reads at most 256 KiB plus one byte before refusing
oversize. Valid finite JSON and Unicode remain supported. Source locks, bounded
pre-replacement comparison, atomic replacement, stable IDs and monotonic history
rules are unchanged.

The former test that required actual shipped history to stay empty forever is
now split into an explicit empty-baseline fixture test and invariants against
the complete actual registry/history chain. This allows a legitimate first
release without dropping the baseline assertions. The new full forward/inverse
test verifies nonempty history against real source objects.

Local evidence for that predecessor: **1,303 Python 3.12 catalog/admin/real-rule-example
tests passed**, and **57 Python 3.13 source/runtime tests passed**. Published
predecessor `1e4fba61ebf9ae15e7cf000c1e2533b63e8b0c49` subsequently passed all
25 individually inspected exact-source CI jobs; it is not live deployment
evidence or acceptance of the current request/preparation batch.

## Implemented owner request and signed history

`AdminCatalogPublications` binds to the existing independent audit client/table
and the same `AdminCatalogDrafts` instance used by the owner routes. Every route
requires the actual owner verifier and fresh MFA. POST routes also require the
existing same-origin check. Signed `catalog.draft` or `audit.receipt` read intent
is persisted before reading publication state, drafts or source. The existing
`rule_publishing` capability remains false; preview, requests and history have
separate feature fields.

The service rereads the owner's signed review and draft, regenerates the full
current metadata manifest, and invokes the existing forward or latest-inverse
compiler against the current history. Exact source provenance comes from the
bounded, strictly decoded packaged `_release_source.json`, checked against the
current release-contract manifest. Browser fields and environment variables
cannot supply the authoritative source commit. Historical request readers remain
usable without that marker or a worker credential. Drafts/reviews remain owner-bound and do not
constitute independent reviewer approval.

| Route | Meaning |
| --- | --- |
| `POST /v1/admin/catalog/publications/preview` | Regenerate source-bound proposal and report global-control availability; create no intent |
| `POST /v1/admin/catalog/publications` | Retained writer: explicit `PUBLISH` or `REVERSE` plus review suffix, reason and action reference; atomically reserve one confirmed request |
| `GET /v1/admin/catalog/publications/{request_id}` | Independently verify owner-bound signed intent and attained preparation stage |
| `POST /v1/admin/catalog/publications/recover` | Read the original request using its retained action reference; never submit, claim or retry work |

The signed `catalog-publication-v1` intent binds the complete request hash,
review/draft hashes, exact base source, current/next registry versions, full
metadata fingerprints, target, operation and compiler plan. Global control is
acquired atomically with the immutable intent. Stable owner/action-reference
replay returns the same verified request before rereading current source;
changed payload at the same reference conflicts. A later source outage therefore
does not destroy historical recovery or authorize a replacement request.

The browser first requires the independently verified manifest. It presents the
operation, target, typed confirmation, reason and stable action reference. It
validates the request receipt against the selected review/source/metadata and
independently rereads the persisted request. A lost reply retains the reference
for read-only recovery; refresh cannot create a second submission. Selection
changes, refresh and logout invalidate late results, and logout clears the
retained form state. The immutable preparation receipt retains `remote_published`, `ci_verified`
and `deployed` as false. Later progress comes only from the separately signed
lifecycle chain described below; the preparation receipt is never rewritten
into a deployment claim.

## Implemented external source preparation

`catalog_publication_worker.prepare_publication` consumes one exact request ID
outside the request-serving Lambda. It verifies the signed intent and original
review/draft, fixed repository origin, clean fetched main at the exact base, and
full source provenance. An acknowledged compare-and-swap control claim alone
permits preparation. A fresh detached worktree runs the existing compiler and
actual source/app/Engine acceptance, then retains a local candidate commit.
The worker verifies the result and records a signed preparation receipt binding
the candidate source SHA and history bytes to the original intent. It never
moves main, pushes, dispatches CI or deploys; browser input cannot choose its
paths, commands, credentials or detector code.

The preparation primitive remains manual and exact-ID. The bounded release
runner below can select the one current signed request without scanning or
requiring an operator to copy its identifier. The standalone audit-key-file CLI requires POSIX
permission verification and refuses non-POSIX use; the callable worker can use
an appropriately injected signed-state adapter on other platforms. A claimed,
interrupted or uncertain attempt remains occupied for inspection. Read-back
after an uncertain claim is never permission to retry source preparation.

## Complete bounded release lifecycle

The new `catalog-publication-lifecycle-v1` contract keeps per-request signed
control and immutable remote, CI, deployment and completion receipts in the
existing independent audit table. Each effect needs its own acknowledged
full-document conditional claim. Unknown claim/write outcomes retain control;
reading an in-progress state does not authorize replacement execution.

`catalog_release_worker` consumes the retained exact history-only candidate. It
checks its source, parent, tree, original review and history against the signed
preparation receipt, then performs at most one non-forced push to the fixed
GuardOath `main`. Browser input cannot select a repository, branch, executable,
cloud profile or path. It never dispatches a CI rerun. The next stage enumerates
all seven exact-candidate workflows and every required individual job; a newer
failed/pending run cannot be replaced by an older success, and partial evidence
does not accept a release.

After CI succeeds, one acknowledged deployment claim binds a fresh invocation
ID and private receipt destination. The worker runs that exact candidate's
`Deploy-Verified.ps1`, which preserves a compatible backup and uses the captured
original Lambda revision for the upload. Its strict machine receipt binds code,
backup, original/upload/completed revisions, configuration, exact resources and
full backend/dashboard/assets parity. The normal terminal path consumes these
just-observed operation-bound results without repeating the entire AWS/asset
pass. An interrupted deployed-stage recovery uses independent readback because
its prior operation ended before terminal evidence persisted.

AWS's asynchronous revision transition is represented honestly. The completed
revision may differ from the acknowledged upload revision; exact acknowledged
`LastModified`, digest and unchanged full configuration still bind completion.
An observed revision never becomes rollback authority. No failed/unknown
deployment emits a success receipt or grants another deployment attempt.

A verified terminal chain permits the **next** legitimate owner request to
atomically replace the old global `SOURCE_PREPARED` control while inserting its
new immutable intent. There is no delete, expiry or administrative reset that
bypasses uncertainty. Earlier owner/reference recovery remains verifiable after
control replacement. The successor proposal must use the currently deployed
source, version and full metadata fingerprint; a stale deployed app cannot
reserve another release just because an older release completed.

The owner UI presents remote publication, all-job CI, deployment source/assets
and completed release evidence distinctly. **Review another release** first
rereads the prior completion and current control, then requires a fresh signed
draft/review. It does not reuse a prior confirmation. Terminal release means
this metadata publication completed, with
`production_live_acceptance=false`; it is not a claim of all fifteen live areas
or general category/check/rule authoring.

### Operator entry point and activation

`process_next_publication` / `--next` reads only the one signed global control
and processes at most one current request. Empty or completed control is idle.
It does not scan, perpetually poll, reclaim uncertain work or provision a cloud
scheduler. An authorized operator must invoke the worker; a browser request does
not itself start GitHub/AWS operations. The CLI exposes its prerequisites before
credential use.

The release supports both `catalog-publication-v1` and
`catalog-publication-lifecycle-v1`.
`ENABLE_NEW_CATALOG_PUBLICATION_STATE=False` and
`ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE=False` remain source controlled.
The route, service, original state and release worker require both gates; the
new lifecycle adapter also refuses writes while its gate is disabled. Deploy an
accepted baseline that understands both contracts before enabling the complete
workflow in a later tested source release. No environment override substitutes
for that compatible rollback boundary.

The original preparation-stage evidence below is historical. Current batch
results and deployment status are recorded in the owner handoff; earlier CI
failures remain preserved rather than silently converted to successes.

Local evidence includes **1,624 broad tests** and the final **259-test focused
suite**, including 25 worker cases and the real mounted ASGI baseline and
retained-writer journeys: customer, missing-MFA, stale-MFA and forged signed
identities are denied before state/source reads; origin and audit failures also
block access. Reopened storage independently verifies seven baseline and eleven
writer signed read receipts, with one writer intent/transaction and unchanged
catalog and tenants. External transports and the harness source marker are
synthetic. At committed source `240355368c70bd8eb69c62bed3ce03e345dcf664`,
actual CI Chrome passed 11 baseline journeys, 9 retained-writer journeys,
19 invocation journeys and all 567 default cases, including the 30 new
publication cases. Local browser download/socket restrictions remain separate
from that CI evidence. The first full CI attempt passed 24 of 25 jobs: an
existing Windows Python 3.12 privacy redaction case withheld its preview at
532.51 ms total under the unchanged 500-ms deadline. This is recorded with
the exact job and bounded recheck decision in the
[owner handoff](admin-console-handoff.md). That first attempt is not release
acceptance; current-main exact-SHA CI and all live acceptance gates still apply.

General category/check/rule creation and behavior authoring remain unfinished.
The complete 47-target roadmap, stable IDs, tenant Off settings, host-signed
reference authority, attachment hard Blocks, all 15 live checks and the original
500-ms PII deadline remain unchanged. No new cloud resource or purchase is
required by this batch.
