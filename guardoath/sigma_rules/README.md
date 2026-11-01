# SigmaHQ event rules

This isolated importer evaluates structured Windows process-creation or PowerShell
script events. It is not registered in the hosted checker, and these event rules
do not establish AI prompt safety or production effectiveness.

Batch 001 contains three process rules. Batch 002 adds 25 PowerShell rules from
SigmaHQ commit `05d1031f537e830b76da53bae2400c5814c89165`. Batch 003 adds another
25 PowerShell rules at the same revision. Batch 004 adds 25 more (**78 total**). All original
batch-002/003/004 YAML, metadata, detections, authors and false-positive notes are retained;
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
Director review before deployment. The 78 imported rules do not require regex.

Reproduce the source audit with a SigmaHQ checkout at the pinned revision and the
optional development dependency PyYAML (not required by runtime or pytest):

```sh
python -m guardoath.sigma_rules.verify_sources /path/to/SigmaHQ/sigma
pytest tests/test_sigma_rules.py tests/test_sigma_import_validation.py tests/test_sigma_upstream_batch_001.py tests/test_sigma_upstream_batch_002.py -q
```

Use `pytest tests/test_sigma*.py -q` for all batches and condition contracts.
The verifier audits all pinned batches (002/003/004: 75 rules), including every original
metadata and detection field and original Git blob ID. Batch 001 predates the
immutable snapshots and is explicitly outside that verifier's count.

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
