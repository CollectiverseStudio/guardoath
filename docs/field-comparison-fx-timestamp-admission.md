# FX reference timestamp admission

FX rate_asof requires an explicit valid offset (hours00–23/minutes00–59), matching the runtime timestamp contract. Malformed offsets such as +00:60 are rejected before typed rule/Policy/TenantRecord admission and revisioned authenticated save. Rejected settings do not alter stored configuration, revision or usage of either tenant. Z and valid positive/negative offsets remain supported. This does not attest exchange-rate truth or impose a new freshness policy.

Verified baseline models blob502251a3741b6efda034e91449a80309bad2f260:12 regressions failed/6validcontrols passed. Fixed18 newcases plus affected suites346 passed17.96s,no skips. Draft fixtures were corrected to use active checks, hard demo budgets and the revision after application startup before measuring that baseline. No acceptance assertions were weakened.
