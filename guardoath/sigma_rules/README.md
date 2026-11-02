# SigmaHQ event rules

This isolated importer evaluates structured Windows process-creation or PowerShell
script events. It is not registered in the hosted checker, and these event rules
do not establish AI prompt safety or production effectiveness.

Batch 001 contains three process rules. Batch 002 adds 25 PowerShell rules from
SigmaHQ commit `05d1031f537e830b76da53bae2400c5814c89165`. Batch 003 adds another
25 PowerShell rules at the same revision. Batches 004 and 005 add 25 each; batch 006 adds another 25 (**128 total**). All original
batch-001/002/003/004/005/006 YAML, metadata, detections, authors and false-positive notes are retained;
the batch JSON files add source paths, Git blob IDs, SHA-256 digests and
GuardOath-authored synthetic positive/benign events. These fixtures are not upstream
tests. Several rules detect legitimate administrative behavior; a finding is not
proof of malicious intent. Do not suppress actual upstream overlaps to force a
single finding. Batch 004 explicitly records overlaps between AD replication and
DSInternals, and between rundll obfuscation and the older suspicious-keyword rule.
A negative control for one specific rule may still match another generic signal.
An additional history-removal fixture asserts both upstream history rules.

Rules use the [Detection Rule License 1.1](LICENSE.Detection.Rules.md). Findings
must retain the original author. Batch-002 findings can use immutable source URLs:

```python
import json
from pathlib import Path
from guardoath.sigma_rules import import_rule

batch = json.loads(Path('guardoath/sigma_rules/upstream_batch_002.json').read_text())
record = batch['rules'][0]
rule = import_rule(record, record['source_path'], source_revision=batch['source_revision'])
if rule.matches(record['positive_event']):
    print(rule.finding())  # Original ID, title, author, license and pinned source URL.
```

The caller must route events by the original `logsource`; matching does not infer
Windows product/category from text. Field names are case sensitive. The supported
import subset is nonempty mappings (AND), lists of mappings (OR), string values
and nonempty string lists (OR), literal case-insensitive equality/contains/
startswith/endswith, `all` over string lists, and Python-compatible case-sensitive
`re` (explicit inline flags are allowed). Conditions support parentheses, not/and/or,
and `all of` / `1 of` selection-name patterns. Validation checks all branches before
matching; invalid event containers raise `UnsupportedSigmaRule`, and missing or
non-string event field values do not satisfy a string comparison.

Wildcard **values** and `windash` imports are now rejected explicitly: the older
helper's fnmatch/slash substitution did not reproduce Sigma semantics. Null/numeric
values, empty selections/lists, timeframe/correlation, unsupported modifiers and
modifier combinations are also rejected. This is conservative compatibility, not
full Sigma support. A regex must compile with Python `re` and be no longer than
512 characters. Regex event strings are limited to 32,000 characters. Search runs
in a disposable isolated Python interpreter with a one-second worker deadline;
the worker is killed and reaped on timeout. This preserves the exact stdlib `re`
semantics, including inline flags, Unicode classes and literal syntax that can
mean something different in another regex engine. Patterns/events travel over
stdin, not process arguments, and worker stderr is suppressed.

Timeout raises `SigmaRegexTimeout`, a subclass of `SigmaEvaluationError`; size
violations or unavailable/failed workers also raise `SigmaEvaluationError`.
These errors mean **incomplete evaluation**, never a clean event or a negative
detection. Callers must surface that distinction before hosted integration.
Invalid/oversized rule patterns remain import-time `UnsupportedSigmaRule` errors.
The deadline includes worker communication/execution after process launch; OS
process creation itself is subject to platform scheduling. Each regex search
starts an interpreter, so process overhead and aggregate per-check budgets need
Director review before deployment. The 128 imported rules do not require regex.

Reproduce the source audit with a SigmaHQ checkout at the pinned revision and the
optional development dependency PyYAML (not required by runtime or pytest):

```sh
python -m guardoath.sigma_rules.verify_sources /path/to/SigmaHQ/sigma
pytest tests/test_sigma_rules.py tests/test_sigma_import_validation.py tests/test_sigma_upstream_batch_001.py tests/test_sigma_upstream_batch_002.py -q
```

Use `pytest tests/test_sigma*.py -q` for all batches and condition contracts.
The verifier audits all pinned batches (001/002/003/004/005/006: 128 rules), including every original
metadata and detection field and original Git blob ID. Batch 001 was added to the pinned audit after its exact source projection was
independently proved; all 128 are now covered.

Batch 003 covers browser-login-data copying, Active Directory module loading,
DNS NRPT configuration, Windows capability/feature changes, ADSI/dMSA account
creation, AMSI patterns, AS-REP discovery, audio-encoding patterns, history clearing,
scheduled tasks, COR profiling, shadow copies, VM/directory discovery, COM download
objects, credential vault access, remoting, ETW changes and certificate exports.
It uses the existing supported subset; no matcher approximation or extra modifier
support was needed. Compound detections require all upstream terms and preserve
upstream OR branches and exclusion filters. For example, the scheduled-task rule
excludes the original cmdletization-module signature only when all its filter
terms are present. This is not a general authorization exemption.

Director integration is required for registry/tenant controls, event routing,
packaging the JSON/YAML/license assets, attribution in user-visible findings, and
UI examples. No hosted integration or deployment is requested by this batch.


Batch 004 adds collection/bookmark discovery, assembly/archive operations, service
ACL and AD inventory/replication, Defendnot installation, hotfix discovery, ICMP/DNS
exfiltration patterns, module loading, unsigned AppX installation, remote commands,
obfuscation chains, keylogging, storage live dumps, dMSA link changes, Group Policy,
MSXML COM and NTFS stream writes. These are upstream event signals, often also
present in legitimate administration. All upstream exclusions and overlaps remain.
No new Sigma modifier/parser semantics are claimed.

## Bounded event/check API (source only)

```python
from guardoath.sigma_rules.events import evaluate_event
from guardoath.sigma_rules import SigmaEvaluationError

# The host must authenticate, authorize and route the original logsource first.
try:
    findings = evaluate_event(imported_rules, event, max_workers=4, timeout_seconds=1.0)
except SigmaEvaluationError:
    # Incomplete evaluation: hold/reject/error according to the service contract.
    # Never turn this into an empty finding list or permission to proceed.
    raise
```

Call this once with the complete caller-routed rule set for an event/check. It
accepts 1–128 imported rules and at most 256 string-named JSON-scalar event fields,
with each string at most 32,000 characters. Settings are trusted host configuration,
not event content: 1–16 worker launches and a positive deadline of at most 5 seconds;
defaults are 4 launches and 1 second shared across the entire call. No rule set is
silently truncated. Larger catalogs need an explicitly budgeted routing design.

`SigmaBudgetExceeded` is a `SigmaEvaluationError`. Nested matcher calls inherit
and cannot reset/enlarge the active budget. Each regex gets at most the lesser of
its one-second ceiling and the remaining event time. Worker launch failures count
against the cap. A worker that returns after the deadline (including OS startup
delay) cannot produce a clean result. Failed work raises instead of returning a
partial finding list. Independent events receive fresh budgets.

Direct `matches`, selection and value calls also establish a budget, but looping
over separate top-level calls is **not** aggregate event enforcement. Hosts must
use `evaluate_event` once or explicitly maintain one outer budget; do not restart
budgets per rule/chunk/retry. The synchronous deadline checks cannot preempt OS
process creation or arbitrary non-regex Python work. A production gateway still
needs an outer request deadline, concurrency/rate limits, original-logsource
routing, tenant entitlement checks and installed-distribution provenance tests.
None of those Director-owned integrations is implemented by this isolated API.

## Batch 005: administration-pattern evidence

`upstream_batch_005.json` adds 25 source-verified PowerShell script rules from
SigmaHQ revision `05d1031f537e830b76da53bae2400c5814c89165`, bringing the imported
inventory to 103 rules. Original YAML, IDs, authors, paths, licensing and detection
semantics are preserved. No new Sigma constructs are approximated or enabled.
`upstream/batch_005/` contains exact source bytes; `verify_sources` now verifies
100 pinned rules (the original three predate immutable snapshots).

`batch_005_evidence.json` supplies each new rule's positive structured-event fixture
and nonmatching look-alike for a future Director-owned registry integration. These
are synthetic fixtures, not upstream tests or measured effectiveness. Legitimate
administration can match: local-user changes, remote sessions, certificate installs,
ACL changes, log clearing and downloads all require operational context. Tests
exercise source exclusions, alternatives, conjunction removal, malformed input,
missing fields and explicit overlaps across all 103 rules. The broader ACL rule
also matches the specific-path rule's look-alike; local benignity is not global.

This evidence does not register a hosted control, chat detector or tenant action.
The existing single-event helper's 128-rule ceiling and shared budgets still apply;
callers must route the original logsource and handle incomplete evaluation as error.

## Complete rule-level evidence handoff

`evidence_catalog.load_evidence_catalog()` returns 128 immutable `RuleEvidence`
records for the Director's rule-level registry work. It retains original IDs,
authors, license, source URLs and logsource; includes original descriptions,
upstream status/level/false positives, positive events, nonmatching look-alikes
and explicit limitations; and verifies all 128 pinned Git-text snapshots offline.
The original three now retain full metadata and immutable source URLs after the
separate C6 pinning handoff. Generic unpinned inputs still retain null provenance
and missing notes rather than fabricated source evidence.

```sh
python -m guardoath.sigma_rules.evidence_catalog > sigma-evidence.json
```

The JSON export marks `runtime_registered=false`. `as_dict()` creates a fresh
copy; editing it cannot change the evidence or tenant policy. The loader reads
fixed package resources, rejects duplicate JSON keys, unknown source/license,
malformed fixtures, duplicate IDs/paths, corrupt snapshots and oversized metadata.
It fails atomically instead of returning a partial catalog. There is no network,
checker execution, tenant selection, action map, checkbox or hosted route. The
metadata bound (4096) is separate from the 128-rule event-evaluation ceiling.

Tests execute each exported positive/look-alike against its original rule and
check all missing-field contracts. A nonmatch for one rule is not global benignity.
Full upstream YAML-to-JSON semantic fidelity remains the development source audit
(`verify_sources` with a pinned upstream checkout), not a new runtime YAML parser.
Director owns registry/tenant integration and package-data declarations; a local wheel probe found all Sigma JSON/YAML/license assets absent. The module
fails closed when imported from that wheel, with no source-checkout fallback.
Director acknowledgement is requested for the reserved `pyproject.toml` package-data
fix and installed-distribution tests before any installed/hosted integration.

## Trusted structured-event routing (isolated)

`routing.load_routing_table()` verifies shipped snapshots and prepares copied
rule detections with original logsource metadata. The trusted host supplies
`trusted_logsource={"product":"windows","category":"ps_script"}` (or
`process_creation`) and a nonempty explicit `allowed_rule_ids` sequence. These
values must come from authenticated host configuration and tenant entitlements,
never event fields. The helper does not authenticate or authorize a caller.

```python
from guardoath.sigma_rules.routing import load_routing_table

table = load_routing_table()
# trusted_source and permitted_ids come from host configuration, not event JSON.
findings = table.evaluate(event, trusted_logsource=trusted_source,
                          allowed_rule_ids=permitted_ids)
```

Selection preserves configured ID order, original product/category and finding
attribution. Definitions remain available through `source_for()` as prerequisites;
unknown source selectors such as `service` are rejected rather than ignored.
Unknown/duplicate IDs, invalid source, no configured matching route and more than
128 selected rules are explicit errors, not clean events. A larger bounded
allowlist may include multiple logsources; the 128 ceiling applies after exact
routing, with no truncation. Event text cannot choose its route or expand the IDs.

Routing validation and one complete `evaluate_event` call share the same outer
deadline/worker budget. Nested callers cannot enlarge it; late workers and later
failures never return partial findings. Missing event fields remain nonmatches
under the original Sigma semantics. The host still needs verified log ingestion,
permissions, concurrency limits and an outer request deadline. This is not a
hosted route, tenant action map or chat validator. The existing installed-wheel
asset blocker must be fixed before distribution-based use.

## Wheel asset acceptance contract

`python -m guardoath.sigma_rules.verify_distribution PATH.whl` inspects only that
archive, without importing archive modules, extracting files or using checkout
assets as a fallback. Success emits JSON with rule/pinned-source/required-asset
counts, source revisions and the canonical retained DRL license digest; exit codes
are 0 for asset-contract success, 1 for verification failure and 2 for bad usage.

The current contract requires all six source-batch JSON files, all 128 referenced
original YAML snapshots and the exact DRL license (135 assets). It rejects missing
or changed assets, duplicate JSON names, nonfinite JSON, unindexed batches/YAML,
ambiguous/case-colliding/traversal member names and bounded resource violations.
UTF-8 Git text is canonicalized for Windows checkout line endings. There is no
partial success. Complete test archives contain deliberately non-executable
module sentinels, proving that acceptance does not run wheel code.

This is a data/licensing consistency gate, not proof of upstream authenticity,
archive-code safety, installed runtime behavior or release approval. Run the
separate pinned-upstream source audit as well. After Director fixes package-data,
verify the actual built wheel with this command, then test trusted installed
`load_evidence_catalog()` and `load_routing_table()` outside the checkout with no
PYTHONPATH fallback. Until those pass, installed/hosted integration remains blocked.

Batch 006 adds 25 further compatible originals. See [its source/evidence handoff](../../docs/sigma-batch-006.md). The default catalog and trusted routing now include all 128 rules (128 pinned); evaluation still rejects more than 128 selected rules.

## Original-three pinning (C6)

[Pinning evidence](../../docs/sigma-batch-001-pinning.md) records the separate
reviewed restoration of complete original metadata and exact snapshots. IDs,
authors, detection semantics and existing events are unchanged. The pre-pinning
projection and audit stay frozen in test fixtures; no historical import date is
inferred. All 128 shipped rules now verify against immutable upstream Git blobs.

## Routing configuration preview

`RoutingTable.plan(trusted_logsource=..., allowed_rule_ids=...)` now uses the same
bounded selector as evaluation and returns immutable selected/source-excluded ID
metadata without examining an event or launching detectors. JSON copies carry
`evaluation_performed=false`; no plan is accepted as an authorization shortcut.
A later evaluation resolves current trusted host inputs again under one aggregate
budget. See [routing-plan contracts](../../docs/sigma-routing-plans.md) for invalid
configuration, ordering, deadline and 128-selected-rule behavior. This adds no
hosted route, policy authority, model connector or rule definition.

## Reviewed release data integrity

The wheel verifier and default shipped catalog/routing loaders check fixed
canonical fingerprints for all six source batches, in addition to original
snapshot digests and license checks. Altered JSON detections or self-rehashed
source claims reject; harmless object ordering, whitespace and Unicode escaping
remain valid. Generic caller-owned builders retain their existing validation.
See [release integrity and anchor updates](../../docs/sigma-release-integrity.md).
This adds no rules, YAML dependency, upstream signature claim or hosted authority.

## Trusted installed-artifact acceptance

`python -I -m guardoath.sigma_rules.verify_installation --expected-root INSTALL_ROOT`
checks exact imported module/metadata origins and non-editable installed records,
then all reviewed source assets and 128 routing/positive/benign/missing/malformed
fixture contracts. It executes trusted installed code only, emits no partial pass,
and never installs or fetches anything. See [the installed acceptance guide](../../docs/sigma-installed-acceptance.md).
Director packaging adoption and release gates remain separate.
