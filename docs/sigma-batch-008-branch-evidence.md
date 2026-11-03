# Batch008 alternate child-process evidence

C18 adds synthetic branch fixtures for the unchanged ClickOnce
`67bc0e75-c0a9-4cfc-8754-84a505b63c04`, DiskShadow
`9f546b25-5f12-4c8d-8532-5893dcb1e4b8`, and HTML Help
`52cad028-0ff0-4854-8f67-d25dfcbc78b4` SigmaHQ rules. All sources remain pinned
at `05d1031f537e830b76da53bae2400c5814c89165` with original metadata,
authors, paths, license and detections. No new rules are registered.

The original primary fixtures cover cmd.exe, powershell.exe and cmd.exe
respectively. The separate `candidates/branch_008_evidence.json` covers the
remaining 16, 7 and 14 original Image suffix alternatives: 37 branches and
185 explicit events. Each branch includes a positive, a child-name `.bak`
look-alike, an unrelated parent, and missing ParentImage/Image events.
For example, a ClickOnce Apps/2.0 parent spawning calc.exe matches; calc.exe.bak
or an unrelated trusted parent does not match that rule.

Dedicated tests compare exact complete findings and provenance under both the
original 178-rule table and current 203-rule table, including case-insensitive
values and script-source exclusion. Reviewed events have precisely their target
finding on positives and no findings on these negatives. This is an observation
for these fixtures, not a general claim that all look-alikes are globally benign.
Every branch also tests malformed values in each essential field. Invalid nested
events reject at the routing boundary; scalar non-string values do not match.
The test imports include the pinned revision so full finding provenance is
checked, not only rule IDs. Exact source SHA256 and Git blob hashes are retained.

Configuration remains explicit trusted host `windows/process_creation` plus
configured original IDs. The 203-rule metadata catalog routes 78 process or
125 script rules, with at most 128 selected in one inherited event budget.
Raw full-catalog evaluation rejects. Branch evidence changes no defaults,
release fingerprints, matcher, packaging, hosted API, tenant policy or budgets.
These synthetic cases measure specific matching branches, not real-world
recall, installed release completeness, hosted adoption or deployment readiness.
