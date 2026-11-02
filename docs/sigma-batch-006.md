# Sigma batch 006: administration and persistence signals

Batch 006 imports 25 original SigmaHQ Windows PowerShell script rules, preserving
all upstream fields and detection logic at revision
`05d1031f537e830b76da53bae2400c5814c89165`. Exact YAML snapshots are in
`guardoath/sigma_rules/upstream/batch_006/`; the batch JSON records their SHA-256
and Git blob hashes. Original author names, IDs, references and logsource
requirements remain attached. The Detection Rule License 1.1 applies; see the
retained license under `guardoath/sigma_rules/`.

These are structured security-event signals, requiring PowerShell script-block
logging and a trusted Windows `ps_script` source. They do not inspect user chat,
execute scripts, establish malicious intent or measure detection effectiveness.
Legitimate administration and quoted commands can match.

## Cases and expected behavior

The batch covers LSASS process lookup, COM shell execution, encoded/hidden
PowerShell invocation, custom web user agents, local-group and share discovery,
Outlook access, disk-image mounting, share deletion, process launching, file
unblocking, PnP discovery, shadow-copy deletion, raw event writes, App-V publishing,
Defender preference removal, network port tests, timestamp modification, profile
modification, service visibility descriptors, VBScript registry writes, MSI
installation, firewall disabling, Winlogon registry modification and WMI
subscription creation.

`upstream_batch_006.json` contains one hand-authored matching event and one
nonmatching look-alike per rule. `batch_006_evidence.json` adds the source links,
use cases, original false-positive notes and limitations. These fixtures are
GuardOath-authored software regressions, not upstream tests or accuracy evidence.

A look-alike is nonmatching for its own rule, not globally clean. Explicit
cross-rule tests record these overlaps:

| Event | Other expected signal |
| --- | --- |
| LSASS and spoolsv process lookups | Existing broad process discovery |
| Local-group rule's `Get-LocalUser` look-alike | Existing local-account manipulation |
| Share-deletion rule's `Get-SmbShare` look-alike | New share discovery |
| Earlier local-account fixtures' group look-alikes | New local-group discovery |
| Earlier mounted-image execution fixture | New disk-image mounting |

Tests preserve upstream literal behavior, including the port-test filter's
whitespace: `-port 443 ` is excluded, but `-port 443` at end of input is not.
Share-module loading is excluded only when every original exclusion marker is
present. No normalization, inferred aliases or broadened matcher semantics were
added.

## Offline use and integration status

Run `python -m pytest tests/test_sigma_upstream_batch_006.py -q` from the checkout
with the test/PII dependencies installed. The suite verifies pinned source hashes,
original YAML-to-JSON equivalence, attribution, positive/look-alike events,
case-insensitivity, required fields and terms, alternative branches, exclusions,
malformed values, unsupported constructs and all cross-rule fixture overlaps.

The six batch JSON files can be supplied together to the existing
`build_evidence_catalog` and trusted-host `RoutingTable` APIs; tests exercise the
complete 128-rule inventory and source-specific routing. Event data must never
choose trusted source or allowed IDs. Direct evaluation accepts at most 128
rules, rejects 129 explicitly, and shares one evaluation budget. Routed script
events select 125 rules; the three process-creation rules stay isolated.

Default catalog/loader integration and artifact verification are coordinated
with the session implementing C1/C2. Until that integration lands, the default
loader still includes batches 001–005 (103 rules); the explicit six-batch tests
and batch 006 handoff cover 128 originals, 125 with pinned snapshots. The original
three rules retain unpinned source status. No hosted rule registration, tenant
settings, packaged-asset fix or deployment is claimed by this import.
