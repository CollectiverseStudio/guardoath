# Signed medication interval arithmetic bounds

A signed medication snapshot must define a representable earliest administration
time: `previous_administration + minimum_interval_minutes`. A valid HMAC does
not make an overflowing date usable authority. For example, a previous timestamp
at `9999-12-31T23:59:59Z` with a 240-minute interval cannot be evaluated within
the supported datetime range. It must be treated as untrusted before evaluating
the submitted document.

Previously that signed snapshot passed reference trust, overflowed during the
comparison, and became an ordinary finding. Observe mode with Flag could then
return complete/Allow. The fix validates the addition in the shared reference
validator. Its existing ArithmeticError handling makes the reference untrusted;
existing Engine handling returns incomplete/Block for an active rule, retaining
the original finding ID. The signing CLI uses the same validator. Explicit Off
continues to disable the selected rule. A latest representable 240-minute
interval starting at `9999-12-31T19:59:59Z` remains trusted.

Synthetic references only. This is a numeric/date representation boundary, not
medical advice or external clinical validation. HMAC, expiry, tenant binding,
actions, security budgets and runtime date limits are unchanged.

Reproduction on the pre-fix actual source: four regression failures and one
valid-boundary control passed. The real Engine independently returned trusted,
complete/Allow for the overflowing snapshot under observe/Flag. After the fix,
the eight new cases and affected authenticated replay, signing CLI, semantic
validity, host-reference, identity, tenant and record-rule suites passed **287
tests in 16.43 seconds**, no skips. Exact published SHA CI is still required; no deployment is
claimed.

The expanded suite runs the real signing CLI in bounded child processes: the overflowing snapshot is refused with exit 2, source-free diagnostics and no output file; the latest valid boundary signs successfully and verifies. Both Engine stages accept an administration exactly at the latest valid threshold.
