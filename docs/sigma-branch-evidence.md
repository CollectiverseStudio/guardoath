# Sigma branch evidence: ten reviewed gaps

This batch adds **30 authored cases for ten branch-evidence gaps in nine existing
rules**. The default inventory stays at 128 original rules. Source YAML,
attribution, detection logic, matcher behavior, routing and evaluation bounds are
unchanged. No hosted control or measured-effectiveness claim is added.

The machine-readable evidence is `guardoath/sigma_rules/branch_evidence.json`.
Every gap identifies its original rule, pinned source revision/hash, branch and
prior evidence gap. Each case contains the submitted event, expected target-rule
result and the explicit complete set of matching IDs among all 128 rules.
Synthetic examples are GuardOath-authored, not upstream tests or accuracy data.

## Review inventory and scope

Reviewed the original positive/look-alike fixtures and dedicated branch tests in
`tests/test_sigma_upstream_batch_002.py` through `006.py`. This is a targeted
review, not an exhaustive code-coverage or semantic-coverage measurement.

| Batch | Existing evidence reviewed | Selected gaps |
| --- | --- | --- |
| 002 | Parametrized direct OR-alternative and case-insensitivity tests | None selected; those simple alternatives already have dedicated evidence |
| 003 | Base examples plus selected alternate/exception cases | CIM scheduled-task exclusion interaction; Opera Login Data path |
| 004 | Base examples plus selected alternate/exception cases | x86 SQL filter; unquoted ipmo Temp; single-quoted Appdata import; PDF collection; ShellSmartScreenLevel; comspec rundll32 branch |
| 005 | Base examples, alternate commandlets, selected exclusions | Legacy chocolatey.org exclusion with Unrestricted |
| 006 | Broad alias/flag matrices and share-module exclusion | Short service SDDL `-sd` argument |

Other branch combinations remain outside this batch, including remaining browser
path, COM-ID, optional-feature, import-path and policy-key alternatives. Passing
these cases does not establish complete coverage of all literal alternatives,
all combinations or real-world detection behavior.

## Gap cases

| Gap | Branch exercised | Expected target behavior |
| --- | --- | --- |
| SB001 | Scheduled-task CIM method and legitimate-module markers | CIM path matches; full exclusion suppresses it; removing any one exclusion marker restores it |
| SB002 | SQL x86 Program Files path filter | Full SQL filter suppresses assembly loading; missing path/text marker or wrong suffix restores the finding |
| SB003 | Unquoted `ipmo $Env:Temp` | Temporary-path import matches; trusted-directory look-alike does not |
| SB004 | Single-quoted Appdata import | Appdata import matches; trusted-directory look-alike does not |
| SB005 | `.pdf` collection alternative | Recursive PDF collection matches; CSV look-alike does not |
| SB006 | `ShellSmartScreenLevel` policy-key alternative | That key matches; unrelated property look-alike does not |
| SB007 | `comspec` rundll32 alternative | Complete invocation matches; changing ShellExec_RunDLL removes this specific finding |
| SB008 | Legacy Chocolatey URL filter | Unrestricted matches; AllSigned does not; exact legacy URL suppresses this rule while a different URL does not |
| SB009 | Service SDDL short option `-sd` | Required descriptor matches; truncated descriptor does not |
| SB010 | Opera Login Data path | Copying Login Data matches; Bookmarks look-alike does not |

An exclusion or look-alike is **not necessarily globally clean**. SB007's
look-alike still matches generic suspicious keywords. SB008's full legacy
Chocolatey exclusion still matches the generic download-capability rule, and the
near-URL case matches both execution-policy and download rules. Full-inventory
expectations retain these signals instead of suppressing them to simplify counts.

## Run and interpret

```bash
python -m pytest tests/test_sigma_branch_evidence.py -q
```

Tests check immutable provenance, target matches, full-128-rule overlaps,
case-insensitive alternatives/exclusions, nonmutation, and trusted script-source
routing with process-source isolation. The complete set is evaluated once with
the existing shared budget; no chunking, budget reset or silent truncation occurs.

Events are ordinary synthetic PowerShell log strings, never executed. They require
trusted Windows script-block logging and host-controlled allowed rule IDs. These
local regressions do not authorize actions or determine whether an administrator's
intent was malicious. Deployment, tenant registration and the outstanding wheel
packaging change remain separate work.
