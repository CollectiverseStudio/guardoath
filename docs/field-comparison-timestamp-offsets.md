# Field Comparison timestamp offset bounds

FC-043 cold-chain duration and FC-050 lifecycle chronology require valid explicit
timezone offsets. Offset minutes must be 00–59 and hours 00–23; valid Z and signed
minute offsets remain supported. In particular, +00:60, +01:99 and -00:60 are
malformed rather than equivalent normalized offsets.

Python datetime.fromisoformat normalizes out-of-range offset minutes. Previously
the shared parser admitted these strings; benign-looking duration/chronology
documents could therefore produce Allow instead of the configured original
finding. The parser now bounds hour/minute syntax before conversion. All existing
calendar/date validation and duration limits remain unchanged.

The same parser validates signed reference timestamps. Invalid offset syntax
cannot become a valid medication reference snapshot; existing untrusted-reference
handling remains incomplete/Block for active signed-reference rules. No host
signature, tenant binding, Off setting, external authority or security budget is
changed. This is representation correctness, not clinical truth.

The new suite exercises both rules on Input/Output for three invalid minute
offsets and Z/+01:59/-00:59 benign controls, plus signed-reference value validation.
On verified original source blob ec6230e9e1b45a3af689437ba7b530a633b6f295,
15 invalid-offset regressions failed and all 12 valid controls passed. The fixed
27 cases and affected conversion/reference/identity/tenant/record suites passed
328 tests in17.13s, no skips. The isolated sandbox asyncio heartbeat runner left
assertions and runtime limits unchanged. An initial lifecycle fixture omitted its
required return pointer mapping; the fixture was corrected before baseline
measurement. No failure of that draft fixture is attributed to runtime.

This patch is privately tested; exact main implementation CI remains required.
