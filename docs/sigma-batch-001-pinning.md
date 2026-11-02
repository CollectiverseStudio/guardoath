# Original Sigma batch 001: immutable pinning

This is the separate integration requested by the [original audit](sigma-batch-001-provenance-audit.md), not a claim that the historical import originally used this revision.

All three definitions are now copied exactly from SigmaHQ commit
`05d1031f537e830b76da53bae2400c5814c89165` using immutable Git objects:

| Rule | Upstream ID | Source path under `rules/windows/process_creation/` |
|---|---|---|
| 7-Zip password compression | `9fbf5927-5261-4284-a71d-f681029ea574` | `proc_creation_win_7zip_password_compression.yml` |
| AccCheckConsole execution | `0f6da907-5854-4be6-859a-e9958747b0aa` | `proc_creation_win_acccheckconsole_execution.yml` |
| Auditpol suspicious execution | `0a13e132-651d-11eb-ae93-0242ac130002` | `proc_creation_win_auditpol_susp_execution.yml` |

The pinning script first compared every previously imported metadata/detection
field with its upstream counterpart. All matched. It then restored omitted
original descriptions, references, dates, tags, false-positive notes and Auditpol
related metadata; retained IDs, authors, titles, status, levels, logsource and
detection objects; and preserved all existing positive/look-alike events.
`upstream/batch_001/` retains original UTF-8 Git text, with SHA-256 and Git-blob
hashes in the JSON matching the independent frozen C4 audit.

The pre-pinning `imported_projection.json`, original `audit.json` and independent
YAML fixtures remain under `tests/fixtures/sigma_batch_001_provenance/`. Tests
compare the frozen historical projection and the current full metadata separately,
and compare all three rules before/after pinning against every current positive
and negative fixture. The source verifier now audits **128/128** pinned originals;
the wheel asset contract now requires **135** assets: six batch JSON files,
128 original YAML snapshots and the retained DRL license.

Run `python -m guardoath.sigma_rules.verify_sources /path/to/pinned/SigmaHQ/sigma`
and `pytest tests/test_sigma_batch_001_pinning.py tests/test_sigma_original_provenance.py -q`.

Findings/catalog exports from the default loaders now use immutable source URLs.
Caller-managed direct imports must still pass the explicit `source_revision`
argument. This does not change Sigma matching support, worker/deadline budgets,
128-rule per-event bounds, tenant actions, hosted routing or detector effectiveness.
The Director's wheel-package-data fix remains a separate required integration gate.
