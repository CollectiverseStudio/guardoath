# Attachment policy rollout — corrected default, legacy tenant review

**Status:** 2026-11-06, implementation on main; production release remains blocked by exact-SHA CI and intermittent local PII 500-ms failures. No automatic tenant bulk update is authorized.

## Runtime contract

- No attachment bytes: do not activate attachment-specific checks.
- Actual attachment bytes: `input_attachment_csv_mode`/`output_attachment_csv_mode` default to `auto` when absent from a tenant record, including records with positive revisions.
- Explicitly stored `off`: skip supported CSV rule scans for that stage and return `not_inspected_policy_off`, never `passed`. Stage/rule Off does not excuse byte-format integrity.
- Unsupported, malformed, unreadable, oversized, truncated, or otherwise incomplete file inspection: always `incomplete` and `Block`, even `mode=observe`, `failure_action=flag`, and stage attachment mode `off`. Do not forward incomplete contents to AI.
- Only currently recognized CSV is inspected. Unsupported PDF/Office/ZIP/images remain blocked rather than cleared.
- Explicitly tenant-enabled prompt, secrets, and PII checks run through the same engine on recognized CSV; configured per-rule choices remain authoritative except mandatory incomplete and unavailable file redaction Block.

## Required legacy saved-data audit (no silent mass migration)

1. Inventory tenant IDs, revision and stored **presence/value** of each attachment-mode field, without copying key hashes, secrets, uploaded content or personal data into the report. Use authenticated administrative access with existing least-privilege controls.
2. **Missing field**: leave absent or explicitly persist `auto` with optimistic concurrency; loading the new model already resolves to Auto.
3. **Explicit `auto`**: preserve.
4. **Explicit `off`**: preserve initially. Earlier releases generated Off for missing legacy fields, and a subsequent save may have persisted that value. Without audited save history these are indistinguishable from intentional opt-outs. Review prior revisions/admin change history/customer intent; change only records positively identified as software-generated legacy Off, with authorized record-level change, audit log and rollback snapshot.
5. Verify each modified tenant's authenticated `GET /v1/tenants/current` and `POST /v1/tenants/current/checks` revision behavior (or supported tenant-checks summary routes) rather than mutating DynamoDB rows without validation.
6. Positive probe: real CSV with synthetic formula must return the expected finding on both Auto stages. Negative probe: no uploaded bytes must not activate file scanners. Mandatory block probes: malformed CSV, supported-envelope oversized content and unsupported PDF must return `incomplete/Block` in observe and with failure_action Flag, including stage Off.
7. Do not deploy unless latest exact-source SHA has 13 individually successful Tests jobs and 3 CodeQL jobs, with Issue #30 PII tail latency resolved and verified. Perform all 15 post-deploy live checks and have rollback ready.

## Evidence / regression

- `guardoath/tenant_record.py` defaults both missing attachment fields to Auto without a revision-dependent Off shim.
- `guardoath/attachment_ingress.py` requires mandatory Block for invalid probe and partial engine inspection.
- `tests/test_authenticated_attachment_ingress.py` checks legacy missing/explicit/mixed modes and observe+Flag+Off incomplete Block.
- Known failing PII job: [Tests 42228089099 / local-pii (3.13)](https://github.com/CollectiverseStudio/guardoath/actions/runs/42228089099/job/127926602314) — synthetic `privacy/date_of_birth1.input`, 560.611ms versus unchanged 500ms; dependent HTML report failure. Green reruns do not close the reliability defect.
