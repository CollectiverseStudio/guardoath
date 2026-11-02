# Original Sigma batch 001 provenance audit

All three imported rules have **identical imported fields and detection logic**
at SigmaHQ commit `05d1031f537e830b76da53bae2400c5814c89165`. Their full metadata
is not identical: the original local imports omit upstream descriptions,
references, dates, tags and false-positive notes; Auditpol also omits `related`.
No rule logic or attribution substitution was needed to establish this match.

The audit fetched that exact commit into a separate Git object database, read
its blobs with `git show <commit>:<path>`, compared every imported upstream field,
and verified SHA-256 and Git blob hashes against the commit's tree. Frozen
snapshots and the complete audit result are under
`tests/fixtures/sigma_batch_001_provenance/`.

| Original rule ID | Upstream author, preserved locally | Exact upstream Git blob |
| --- | --- | --- |
| `9fbf5927-5261-4284-a71d-f681029ea574` (7-Zip password compression) | frack113 | `08f75d9922d9dffb27e3480139fe95c689cc6b5d` |
| `0f6da907-5854-4be6-859a-e9958747b0aa` (AccCheckConsole) | Florian Roth (Nextron Systems) | `94019b6ce65c1886c95b87a494b821f6119d0903` |
| `0a13e132-651d-11eb-ae93-0242ac130002` (Auditpol tampering) | Janantha Marasinghe (https://github.com/blueteam0ps) | `484b02a5de2f8ee421d7c7bdae4cc1b8b4f9d669` |

Source paths, immutable source links, SHA-256 hashes and exact omitted-field lists
are in `tests/fixtures/sigma_batch_001_provenance/audit.json`. The snapshots retain
all original authors and metadata under the [Detection Rule License 1.1](../guardoath/sigma_rules/LICENSE.Detection.Rules.md).

## Repeat the audit

With a local SigmaHQ checkout or bare Git object database containing the commit,
run from a prepared GuardOath development checkout (PyYAML is needed by this
development command):

```bash
python -m guardoath.sigma_rules.audit_originals /path/to/SigmaHQ/sigma \
  05d1031f537e830b76da53bae2400c5814c89165
python -m pytest tests/test_sigma_original_provenance.py -q
```

The helper performs no fetch, checkout, source write, policy change or detector
execution. It reads committed Git blobs even with a dirty upstream working tree.
A full lowercase 40-character commit is required; branch names are rejected.

Exit 0 means all imported-field projections match; omitted upstream metadata is
still reported separately. Exit 1 means at least one projected field differs.
Exit 2 means the audit could not complete. Errors do not print source contents.
The tests cover exact hashes/metadata, source attribution and detection drift,
known omitted fields, real offline Git reads, dirty-worktree isolation, missing
objects and the CLI's exit statuses. Synthetic Git test histories are explicitly
not represented as commits from the actual upstream repository.

## Status and next integration

This audit proves equivalence at one immutable upstream revision. It does not
prove when or from which historical commit the first import was made, nor verify
real-world effectiveness or upstream author identity beyond the source metadata.
The existing positive/look-alike fixtures remain local regression evidence.

**Runtime metadata is unchanged.** The default catalog still has 128 rules,
125 pinned snapshots, and three original entries with null revision/digest and
master-based links. The frozen audit snapshots establish a concrete candidate
source for upgrading those three entries. Restoring omitted metadata, moving
snapshots into the shipped batch assets, adding the immutable revision/digests,
and updating evidence/distribution counters is a separate coordinated handoff.
Do not silently count this audit as completing that integration or the pending
wheel-packaging fix. No hosted registration or deployment is included.
