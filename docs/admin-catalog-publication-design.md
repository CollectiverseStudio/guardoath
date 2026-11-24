# Owner catalog publication: implementation evidence and next integration

Recorded November 24, 2026 (UTC), for #42 and CORE-06 of the preserved
[product roadmap](product-roadmap.md). This document records the real source
authority, the executed forward/reversal acceptance, and the remaining
owner-to-release connection. It does not claim an authenticated publication API,
deployed catalog edit, or completion of #42.

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

## Newly verified real source round trip

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

Local evidence for this batch: **1,303 Python 3.12 catalog/admin/real-rule-example
tests passed**, and **57 Python 3.13 source/runtime tests passed**. Exact published
source CI is still required independently.

## Next coherent implementation: explicit owner publication request

Keep the first connected publication path limited to the supported description
fields of one existing rule. The owner API must re-read its signed review and
draft, reproduce the current manifest and persist a distinct publication intent
after exact typed confirmation, reason and action reference. Recording a review
alone must not promote it to publication authority. Current drafts and reviews
are bound to the same owner; they do not establish independent reviewer approval.
Retain owner-only authorization and do not activate historical delegated roles.

The intent needs the exact base source commit, current registry version and
complete metadata fingerprint, review/draft IDs, operation kind, requested next
version and target. A stable control for the application catalog must serialize
competing release requests. Insert the intent and acquire its expected baseline
atomically; replay or ambiguity must not create a second release. New persistent
formats need their own verified reader and independent source-controlled release
contract, not the cancellation or subscription-cap writer flags.

Proposed routes can express request and history without mislabeling completion:

| Proposed operation | Required meaning |
| --- | --- |
| Request publication of a signed review | Persist a confirmed, source-bound intent; no immediate deployment claim |
| Inspect a publication | Verify immutable evidence and report the actual attained stage |
| Request reversal of the latest published change | Require a fresh inverse review and a new higher-version release |

## Trusted source executor and truthful completion evidence

The application must retain its source-shipped catalog authority. A DynamoDB
"published version" pointer or an altered admin projection would not update all
workers, customer responses and Engine imports together. Do not introduce that
second authority.

A separate trusted release executor should consume an exact verified request
ID, obtain a clean exact-base checkout, invoke the existing compiler and actual
source/app acceptance, and produce the proposed source commit. Browser input
must never choose arbitrary paths, commands, credentials or detector code.
Keep Git/subprocess/deployment privileges out of the request-serving Lambda.

Completion evidence must distinguish the projected metadata hash from the Git
source and artifact. Independently verified receipts should bind:

- Original publication intent and signed review/draft references.
- Exact base and candidate commits, registry versions and metadata fingerprints.
- Release-history bytes and built package digest.
- Complete successful CI jobs for the candidate commit.
- Deployed source and actual live acceptance, only when obtained.

Use distinct states such as requested, source prepared, CI verified and
deployment verified. A browser assertion, local build or simulated deployment
cannot advance the live state. Reuse the existing Lambda builder and verified
deployment/rollback gates; keep a separate catalog evidence artifact if needed
rather than casually changing the strict persistent-state rollback manifest.

Logical catalog reversal follows the same requested/reviewed/tested release
path. Emergency restoration of a verified prior Lambda package after failed
deployment remains the existing separate rollback procedure.

## Remaining completion boundary

This batch fixes source interpretation and verifies a full metadata release
round trip. It does not implement the proposed request/executor/receipt bridge,
general category/check/rule creation or behavior authoring. Those still need
stable-ID/reference constraints, meaningful benign/adversarial/negative/timeout
tests, authenticated browser operation and persistent audit. Source parity,
compatible rollback, all 15 live checks, customer Off preservation, host-signed
reference authority and the original 500-ms PII deadline remain required.
