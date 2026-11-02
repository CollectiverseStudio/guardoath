# Candidate process branch evidence (C15)

This batch adds **34 authored cases across 16 branch/near-miss groups** in the
25 C13 process-rule candidates. The evidence is
`guardoath/sigma_rules/candidates/branch_007_evidence.json`; original rules,
snapshots, authors, detection logic and source hashes are unchanged.

These are software-regression fixtures, not new detectors, effectiveness data,
policy selections, hosted registrations or automatic candidate promotion. C14
separately owns default catalog/release/packaging integration. Tests work with the
same 153-definition review cohort before or after that adoption, without importing
a duplicate candidate or changing trusted configuration.

## Branch subset

The original C13 matching/look-alike pairs did not separately demonstrate these
selected alternatives. This list is targeted, not an exhaustive branch-coverage
or semantic-coverage claim.

| Group | Alternative or near miss |
| --- | --- |
| CP001 | ASP.NET Framework64 image path |
| CP002 | ASP.NET AppData Local Temp compilation path |
| CP003 | NT-resource-kit Auditpol `/process:none` |
| CP004 | BaaUpdate spawning wscript |
| CP005 | BGInfo x86 parent name |
| CP006 | Vivaldi headless image |
| CP007 | Opera extension load with pwsh parent |
| CP008 | Tor Description-only metadata |
| CP009 | CHCP 1258 suffix |
| CP010 | Clip OriginalFileName-only metadata |
| CP011 | Cloudflared cleanup `-config` |
| CP012 | Cloudflared run `-credentials-file` |
| CP013 | Conhost high-integrity SID |
| CP014 | CSI with pwsh parent |
| CP015 | CMD `se^t` caret token |
| CP016 | Headless debugging: remove each required flag independently |

Each group has an independently authored positive event and a look-alike.
CP016 also has two explicit missing-term cases. Additional tests remove required
fields and supply malformed values. Event field names remain case sensitive;
source values retain the original case-insensitive behavior. No process,
clipboard, tunnel, browser or example command is executed.

## Complete overlap expectations

Every case lists the exact matching IDs among all 153 reviewed definitions,
including overlaps beyond its target. ASP.NET trusted-path look-alikes still
match general compiler execution. Opera extension loads with ordinary parents
still match general extension loading. Chromium debugging examples with no
profile or remote-debugging flag still match general headless execution; removing
`--headless` removes both signals. A target nonmatch is not a globally clean event
or a statement that activity is safe.

Tests evaluate every reviewed definition, preserve exact finding attribution,
and verify trusted process routing plus script-source isolation. The review cohort
has 28 process rules and 125 script rules. It still uses the existing shared
per-event budget and 128-selected-rule limit. Direct evaluation of all 153 rejects;
no chunking, truncation, budget reset or relaxed limit is introduced.

## Validation and integration boundary

Run from a prepared source checkout:

```bash
python -m pytest tests/test_sigma_candidate_branch_evidence.py -q
```

Source hashes link each group back to its original immutable SigmaHQ YAML at
`05d1031f537e830b76da53bae2400c5814c89165`, with retained Detection Rule License
1.1 and original attribution. Candidate/default duplicates are permitted only
when the reviewed source metadata and authored base fixtures are identical;
the test cohort contains each rule once. This is a test construction, not a
runtime release-loader fallback or authorization mechanism.

C14 owns independently reviewed manifests, semantic release fingerprints,
archive/installed acceptance and coordinated source-routing integration. The
Director retains reserved packaging/hosted work. This evidence does not resolve
those release requirements or authorize deployment.
