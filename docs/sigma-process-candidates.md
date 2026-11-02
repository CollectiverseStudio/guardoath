# Staged Windows process-creation candidates (C13)

This review stages **25 original SigmaHQ rules** at immutable revision
`05d1031f537e830b76da53bae2400c5814c89165`. Complete original YAML, metadata,
detection logic, authors, source paths and Git/SHA-256 hashes are retained in
`guardoath/sigma_rules/candidates/batch_007.json` and `candidates/batch_007/`.
The Detection Rule License 1.1 applies; see
[the retained license](../guardoath/sigma_rules/LICENSE.Detection.Rules.md).

**These candidates are not part of the default release.** Its inventory stays
128 pinned rules with 135 required archive assets. No release fingerprint, default
batch manifest, package configuration, event budget or hosted control changes.
Source compatibility and authored fixture matches do not establish intent,
effectiveness or production readiness. Legitimate administration can match.

## Candidate inventory

Every candidate has an independently authored matching event and a look-alike
in the JSON file. Original status, severity, false-positive notes and descriptions
are preserved there, rather than replaced with an invented maturity assessment.

| Rule | Original ID | Original status / level |
| --- | --- | --- |
| AspNetCompiler Execution | `a01b8329-5953-4f73-ae2d-aa01e1f35f00` | test / medium |
| Potentially Suspicious ASP.NET Compilation Via AspNetCompiler | `9f50fe98-fe5c-4a2d-86c7-fad7f63ed622` | test / high |
| Interactive AT Job | `60fc936d-2eb0-4543-8a13-911c750a1dfc` | test / high |
| Audit Policy Tampering Via NT Resource Kit Auditpol | `c6c56ada-612b-42d1-9a29-adad3c5c2c1e` | test / high |
| Suspicious BitLocker Access Agent Update Utility Execution | `9f38c1db-e2ae-40bf-81d0-5b68f73fb512` | experimental / high |
| Uncommon Child Process Of BgInfo.EXE | `aaf46cdc-934e-4284-b329-34aa701e3771` | test / medium |
| BitLockerTogo.EXE Execution | `7f2376f9-42ee-4dfc-9360-fecff9a88fc8` | test / low |
| Potential Data Stealing Via Chromium Headless Debugging | `3e8207c5-fcd2-4ea6-9418-15d45b4890e4` | test / high |
| Browser Execution In Headless Mode | `ef9dcfed-690c-4c5d-a9d1-482cd422225c` | test / low |
| Chromium Browser Instance Executed With Custom Extension | `88d6e60c-759d-4ac1-a447-c0f1466c2d21` | test / medium |
| Suspicious Chromium Browser Instance Executed With Custom Extension | `27ba3207-dd30-4812-abbf-5d20c57d474e` | test / high |
| Tor Client/Browser Execution | `62f7c9bf-9135-49b2-8aeb-1e54a6ecc13c` | test / high |
| Suspicious CodePage Switch Via CHCP | `c7942406-33dd-4377-a564-0f62db0593a3` | test / medium |
| Data Copied To Clipboard Via Clip.EXE | `ddeff553-5233-4ae9-bbab-d64d2bd634be` | test / low |
| Cloudflared Tunnel Connections Cleanup | `7050bba1-1aed-454e-8f73-3f46f09ce56a` | test / medium |
| Cloudflared Tunnel Execution | `9a019ffc-3580-4c9d-8d87-079f7e8d3fd4` | test / medium |
| Potential Dosfuscation Activity | `a77c1610-fc73-4019-8e29-0f51efc04a51` | test / medium |
| VolumeShadowCopy Symlink Creation Via Mklink | `40b19fa6-d835-400c-b301-41f3a2baacaf` | stable / high |
| NtdllPipe Like Activity Execution | `bbc865e4-7fcd-45a6-8ff1-95ced28ec5b2` | test / high |
| Sticky Key Like Backdoor Execution | `2fdefcb3-dbda-401e-ae23-f0db027628bc` | test / critical |
| Unusual Parent Process For Cmd.EXE | `4b991083-3d0e-44ce-8fc4-b254025d8d4b` | test / medium |
| CMSTP Execution Process Creation | `7d4cdc5a-0076-40ca-aac8-f7e714570e47` | stable / high |
| Suspicious High IntegrityLevel Conhost Legacy Option | `3037d961-21e9-4732-b27a-637bcc7bf539` | test / informational |
| Conhost.exe CommandLine Path Traversal | `ee5e119b-1f75-4b34-add8-3be976961e39` | test / high |
| Suspicious Use of CSharp Interactive Console | `a9e416a8-e613-4f8b-88b8-a7d1d1af2f61` | test / high |

## What the fixtures establish

Tests verify exact YAML-to-JSON equivalence, immutable blob hashes, attribution,
positive/look-alike behavior, case-insensitive values, case-sensitive field names,
missing/incorrect fields, malformed scalars/containers and rejection of unsupported
constructs even in unused branches. Process logs are data; no example command is
executed. Full metadata retains the original upstream semantics and caveats.

Explicit cross-rule expectations cover all 153 reviewed definitions (128 defaults
plus 25 staged candidates):

| Specific candidate example/look-alike | Broader candidate that also matches |
| --- | --- |
| ASP.NET compiler suspicious path and trusted-path look-alike | General ASP.NET compiler execution |
| Chromium headless debugging and missing-profile-path look-alike | Chromium headless execution |
| Chromium extension load with suspicious and ordinary parents | General Chromium extension loading |

All other authored candidate pairs match only their target or no rule, as stated
in the tests. None of the 128 defaults' current positive/look-alike fixtures match
these new candidates. These finite fixture results do not guarantee absence of
other real-world overlaps.

Run `python -m pytest tests/test_sigma_process_candidates.py -q` from a prepared
source checkout. Individual cases are also checked through the existing trusted
routing API. The default loader rejects candidate IDs; no implicit opt-in occurs.

## Required integration plan

1. Review all original metadata, synthetic examples and known overlaps. Candidate
   evidence is source-reviewed staging, not a deployed detector catalog.
2. Coordinate moving reviewed assets into a new shipped batch and extending the
   default batch manifest. Audit every source against the immutable Git revision;
   freeze a reviewed semantic fingerprint independently of the artifact being
   inspected. Do not teach the verifier to trust a candidate's self-reported hash.
3. Coordinate evidence, routing, release-integrity and distribution expectations.
   Adding all 25 would make the catalog 153 rules. **The event cap remains 128**:
   trusted Windows process routing selects 28 rules and excludes 125 script rules;
   trusted script routing selects 125 and excludes 28 process rules. Tests exercise
   both plans and one shared evaluation per event. Passing all 153 directly to
   the evaluator rejects explicitly; no chunking, truncation or budget reset.
4. Keep product/category and allowed IDs under trusted host configuration. Event
   fields must not choose their route or select unpublished rules. Re-test the
   128/129 boundary and aggregate deadlines/workers without increasing limits.
5. Coordinate the Director-owned packaging patch, required asset inventory and
   release anchors, then rebuild the actual wheel and rerun archive plus fresh
   non-editable installed acceptance. C12 currently validates the 128-rule release;
   staging these candidates does not update that contract or fix missing assets.
6. Require focused/full tests, normal matrix/browser tests and CodeQL on the exact
   integration SHA before release review. Hosted registration, tenant entitlements
   and deployment remain separately authorized work.

The 25 originals were read with `git show <commit>:<path>` from the exact fetched
upstream object database; Git tree blob IDs and SHA-256 hashes match all retained
snapshots. This proves source identity at that revision, not classifier accuracy
or upstream author identity beyond the preserved source metadata.
