# SigmaHQ event rules

This isolated importer evaluates structured Windows process-creation or PowerShell
script events. It is not registered in the hosted checker, and these event rules
do not establish AI prompt safety or production effectiveness.

Batch 001 contains three process rules. Batch 002 adds 25 PowerShell rules from
SigmaHQ commit `05d1031f537e830b76da53bae2400c5814c89165`. Batch 003 adds another
25 PowerShell rules at the same revision (**53 total**). All original
batch-002/003 YAML, metadata, detections, authors and false-positive notes are retained;
the batch JSON files add source paths, Git blob IDs, SHA-256 digests and
GuardOath-authored synthetic positive/benign events. These fixtures are not upstream
tests. Several rules detect legitimate administrative behavior; a finding is not
proof of malicious intent. Do not suppress actual upstream overlaps to force a
single finding. The primary positive/benign fixture matrix has no cross-rule
matches; an additional history-removal fixture deliberately asserts two upstream
matches (the PowerShell and Windows console history rules).

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
full Sigma support. A regex must compile with Python `re`; cross-engine regex
portability and runtime budgets require review before broader regex imports.

Reproduce the source audit with a SigmaHQ checkout at the pinned revision and the
optional development dependency PyYAML (not required by runtime or pytest):

```sh
python -m guardoath.sigma_rules.verify_sources /path/to/SigmaHQ/sigma
pytest tests/test_sigma_rules.py tests/test_sigma_import_validation.py tests/test_sigma_upstream_batch_001.py tests/test_sigma_upstream_batch_002.py -q
```

Use `pytest tests/test_sigma*.py -q` for all batches and condition contracts.
The verifier audits all pinned batches (002/003: 50 rules), including every original
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
