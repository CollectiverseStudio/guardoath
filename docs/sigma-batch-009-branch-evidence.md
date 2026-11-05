# C21 batch009 branch evidence

This batch adds evidence for two unchanged SigmaHQ rules under its published
C21 ownership claim. Current inventory is228 pinned rules/239 assets; the
original203-rule review remains frozen. No imported JSON,
YAML, source IDs, authors, licensing, metadata, selectors or release anchors change.

Five exact gaps are covered: the default PowerSploit/Empire schtasks detection
`56c217c3-2de2-479b-990f-5c109ba8458f` with a pwsh parent crossed with DAILY/ST,
ONIDLE and HOURLY schedules; and Hydra
`aaafa146-074c-11eb-adc1-0242ac120002` with ^PASS^ and missing/restored -u or -p.
The separate branch_009_evidence.json contains38 authored events with explicit
full228-rule findings, including positive, look-alike and essential missing fields.
Tests also cover malformed values per essential field, complete source attribution,
case-insensitive literals, script-route exclusion and one inherited event budget.

Literal semantics matter. Appending _EXTRA to an ONIDLE/HOURLY schedule still
matches the original contains selector, and DAILY/ST has the same containment
behavior. Those cases are positive, not forced false negatives. Hydra's original
-u/-p literals include a trailing space: a switch at end of line or followed by
a tab does not satisfy that literal. No command parser or whitespace normalization
is invented. All fixture strings are synthetic and are never executed commands.
For example, the full ^PASS^ event with both original switches matches Hydra;
removing -u retains ^PASS^ but removes that finding.

Configuration remains trusted host windows/process_creation plus configured
original rule IDs. Process103/script125 source groups and the128-selected-rule
one-event limit are unchanged. These finite fixtures prove specific matching
branches, not real-world recall, generic chat validation, tenant authority,
installed completeness, hosted adoption or deployment readiness. Source revision
05d1031f537e830b76da53bae2400c5814c89165 and exact source digests remain explicit.
