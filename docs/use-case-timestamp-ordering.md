# Timestamp ordering — use cases and acceptance contracts

**Category:** Structure / Timestamp ordering. **Existing checker:** `timestamps` in `guardoath/data_quality.py`. **Policy:** `policies/timestamps-batch.json`. The new cases are regressions against established behavior, not a new detector or live-deployment assertion.

## Intended behavior

The tenant submits JSON with an ordered `samples` sequence. Each sample contains an ISO 8601 timestamp with an explicit offset or `Z` and a numeric value. The check converts each timestamp to a UTC instant for comparison, but **does not reorder the submitted samples**. With `allow_equal=false`, adjacent instants must be strictly increasing. A reversed pair or equal pair produces `structure.timestamp_order`.

| Use case | Violation or benign example | Expected outcome |
|---|---|---|
| Offset equivalence | 10:00+01:00 and 09:00:00Z denote the same instant | Finding when strict order requires later time |
| Valid increasing offsets | 10:00+01:00 then 09:00:01Z | Complete Allow |
| Microsecond ordering | 09:00:00.000000Z then 09:00:00.000001Z | Complete Allow |
| Out-of-order samples | 09:00:02Z then 09:00:01Z | `structure.timestamp_order` |
| Intermediate regression | 09:00:00Z, 09:00:02Z, 09:00:01Z | `structure.timestamp_order` |
| Duplicate decoded JSON key | The `samples` key appears twice | Incomplete; `timestamps_invalid_document`; fail-closed Block |
| Truncated JSON | A sample is not terminated | Incomplete; `timestamps_invalid_document`; fail-closed Block |
| Impossible calendar date | 2026-02-30T09:00:00Z | Incomplete; `timestamps_invalid_document`; fail-closed Block |
| Invalid data type | Sample value is Boolean instead of numeric | Incomplete; `timestamps_invalid_document`; fail-closed Block |
| Incompatible message format | Text rather than JSON | Skipped required checker; incomplete; fail-closed Block |

## Tenant configuration and sample

The existing `timestamps-enforce-v1` profile uses `mode=enforce`, required `timestamps` validation, `allow_equal=false`, and `max_samples=200`. Completed Input ordering findings trigger Flag; completed Output ordering findings trigger Block. Required-check errors or skips are incomplete and use the default Block failure action on both stages.

Example benign payload:

```json
{"samples":[
  {"timestamp":"2026-11-04T10:00:00+01:00","value":12},
  {"timestamp":"2026-11-04T09:00:01Z","value":13}
]}
```

The `value` field must be a JSON number (not Boolean); the checker does not interpret its domain significance. This check makes no claims about clock synchronization, input provenance, fresh telemetry, business authorization, cross-message ordering, or whether the timestamps were fabricated.

## Evidence and release boundary

- `tests/test_timestamp_order_regression.py` exercises the above cases on both Input and Output using the production checker/Engine in enforce mode, including concrete finding IDs and effective actions.
- `tests/test_data_quality.py` and `evals/timestamps.json` cover broader existing API and saved-evaluation contracts.
- `docs/data-quality-coverage.md` documents the base check, supported syntax and bounded resource policy.

The regression file must pass its focused tests and the exact-implementation 13/13 repository Tests plus 3/3 CodeQL before any release claim. No new checker, API, tenant schema, dashboard, CI, paid service or AWS deployment was introduced by this test batch.
