# Batch 007 default source adoption

C14 adopts the 25 exact Windows process-creation originals independently reviewed
in [C13](sigma-process-candidates.md), candidate commit
`95eca0339f2313690babb83c8d236064d1ee140f`. Candidate files remain frozen review
history. Every adopted rule row and YAML snapshot is identical to that handoff;
only the batch envelope marks the separately reviewed default-source adoption.
Original IDs, authors, paths, license, complete metadata and detections remain
unchanged, including lists, conjunctions and source limitations.

All originals were independently compared with immutable Git blobs at SigmaHQ
revision `05d1031f537e830b76da53bae2400c5814c89165` before adoption. Full metadata,
SHA-256/Git-blob digests and positive/look-alike outcomes matched. The full source
audit now covers **153 originals**. The first six release fingerprints stay
unchanged; batch007 has its own reviewed canonical fingerprint. The default
archive/installation contract now requires **153 pinned rules / 161 assets**:
seven batch JSON files, 153 original YAML files and the retained DRL license.

Use cases include ASP.NET compilation, scheduled interactive execution,
audit-policy switches, process ancestry, browser headless/extension modes,
Tor/clipboard utilities, Cloudflared tunnels and command-line patterns. Several
are legitimate administration or utility use. Fixtures are authored regression
evidence, not upstream tests, measured effectiveness or malicious-intent proof.

The larger catalog does not expand event work. An authenticated host selects the
original source, then the router calls the existing evaluator exactly once:

| Trusted source | Selected rules | Known configured rules excluded |
| --- | ---: | ---: |
| Windows `ps_script` | 125 | 28 |
| Windows `process_creation` | 28 | 125 |

Each event still permits at most 128 selected rules under one inherited deadline
and worker quota. Raw evaluation of 153 rules or a 129-rule single-source selection
rejects explicitly, without truncation, chunking or partial success. Synthetic
growth tests exercise these boundaries. Real mixed-source payload tests prove
that event content cannot select both groups or override trusted host routing.

Candidate tests retain their frozen 128-rule review baseline and explicit proposed
153-rule overlap matrix. Current default tests additionally compare every prior
positive/look-alike finding with the actual old 128-rule release, verify complete
source partitions and exact finding attribution, and cover all candidate
malformed/missing fields and unsupported dead branches. The reviewed ASP.NET,
Chromium headless and extension overlaps remain visible; negative fixtures for
one rule can still match another source rule.

Run `pytest tests/test_sigma* -q` and the full relevant suite, then the source,
wheel and installed acceptance commands described in the existing guides.
This adds no tenant, engine, hosted route, model connector or deployment. The
Director's reserved packaging adoption remains a release blocker; its inert
package-data proposal already uses glob patterns that include batch007, but the
actual adopted wheel must be rebuilt and tested with 153 rules/161 assets and
exact-SHA CI/release gates.

The unchanged inert package-data proposal was rebuilt in an isolated copy with
batch007: its real wheel passed 153/153/161 archive verification, then a fresh
non-editable offline venv installation outside the checkout passed
`python -I -m guardoath.sigma_rules.verify_installation --expected-root ...` with
153 each of positive, benign, empty-event, malformed-rejection and routing-plan
checks. Actual repository packaging is still unchanged; this is proposal evidence
and does not establish release readiness for its unmodified wheel.
