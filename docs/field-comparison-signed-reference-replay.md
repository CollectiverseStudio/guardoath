# Authenticated signed-reference replay boundaries

The signed-reference contract binds authority to a tenant, stages and reference
values. A valid MAC for one tenant must not authorize another tenant even when
both use the same rule ID. Observe mode and Flag failure policy do not relax
mandatory incomplete/Block when an active saved reference cannot be trusted.

`tests/test_signed_reference_tenant_replay.py` exercises the real authenticated
API, TenantService, tenant store and cross_field Engine with synthetic FC-047
permit authority. No external permit or clinical correctness is asserted.

The fourteen cases cover Input and Output for a signature copied from another
tenant, expanded stages, changed reference values, expired timestamps, a
correctly signed but invalid NaN reference and a missing host key. A valid
owner control first proves complete/Flag recommendation/effective Allow in
observe mode. The attacked request must retain its original finding ID and
be incomplete/Block; submitted JSON reference/signature fields cannot replace
saved authority. Neither response may expose host keys, signatures or private
source/version metadata. Each authenticated request is metered once for its
owner.

The two explicit Off controls prove the selected stage stays Off while the
other stage still blocks the wrong-owner reference. A cross-tenant application
request returns HTTP 403 without incrementing either owner's usage.

These are regression tests for existing behavior, not a new reference source,
new runtime rule or proof that synthetic reference values are externally
trusted. Existing operator guidance remains in `docs/field-comparison-signing-boundaries.md`
and `docs/user-guide.md`. No runtime, security budget, action semantics or saved
policy is changed. Exact published CI and deployment verification remain separate.

Measured against runtime reconciled to `424d5b8baebed056850b1695fdc5c487a57b24b3`: **279 affected tests passed in 16.32 seconds**, no skips or warnings. The sandbox external asyncio heartbeat runner was used without changing assertions or application limits. The initial fixture used string stages in a model-copy tamper; it was corrected to enum stages to avoid serializer warnings. This was a test-fixture correction, not a runtime defect.
