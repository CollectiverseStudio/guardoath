# Field Comparison reference isolation regression

FC-039 quarantine exclusions and FC-048 authorized scopes use saved tenant
reference values. Submitted JSON cannot replace that authority. Rule identifiers
may be identical across tenants; that must not make the tenants share settings,
findings, action overrides or request usage.

`tests/test_field_comparison_reference_isolation.py` exercises the existing
authenticated tenant API, memory tenant store, TenantService and cross_field
Engine. It introduces no checker, policy store, reference source or runtime
change. All reference labels and tenants are synthetic.

## Covered behavior

- Two valid tenant records use the same scope/quarantine rule IDs but different
  reference sets. Each complete document is allowed by exactly one tenant and
  yields both original findings and Block for the other. Repeated interleaved
  requests detect accidental reference caching by rule ID alone.
- Findings and receipts do not echo submitted/reference values. Submitted
  `trusted_values`, `permissions` and `quarantine` fields cannot replace saved
  policy authority.
- Another tenant's application ID is refused with HTTP 403 before either
  tenant is metered. Authorized checks are counted once for the owning tenant.
- Revisioned Allow/Flag/Off rule overrides change only the selected tenant and
  stage. The other stage and the second tenant remain Block with all findings.
- Serial uniqueness and authorized scopes accept collections of one and 128
  valid members. Empty and 129-member collections produce their original
  findings, including otherwise unique serials and wholly authorized scopes.
- Null, boolean, integer, fractional, object, array and invalid-string members
  fail on both stages without losing either configured finding ID.

## Reproduction and evidence

```sh
pytest -q tests/test_field_comparison_reference_isolation.py tests/test_advanced_identity.py tests/test_advanced_arithmetic.py tests/test_tenant_record.py
```

Reconciled against actual main `8df2540ec70290084b064e338d5a4e0d797cd857`, including the adopted signed-reference security fix. The 34 new isolation/boundary cases plus existing identity, tenant-record and record-rule suites passed **217 tests in 11.13 seconds**, no skips. All runtime changes since the prior measured source were fetched from exact GitHub main into the isolated test source. The external sandbox asyncio heartbeat runner was used; assertions and application limits were unchanged. No runtime files or shared checkout were edited.

This is synthetic authenticated integration evidence. It does not prove that tenant references came from an external authoritative issuer, that a lot is clinically safe, or that scopes were granted by an outside identity provider. Exact published implementation CI remains required; these tests do not establish deployment, PII reliability or production release acceptance.
