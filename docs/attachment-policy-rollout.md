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
5. Verify each modified tenant's authenticated `GET /v1/applications/current` and `POST /v1/tenants/current/checks` revision behavior (or supported tenant-checks summary routes) rather than mutating DynamoDB rows without validation.
6. Positive probe: real CSV with synthetic formula must return the expected finding on both Auto stages. Negative probe: no uploaded bytes must not activate file scanners. Mandatory block probes: malformed CSV, supported-envelope oversized content and unsupported PDF must return `incomplete/Block` in observe and with failure_action Flag, including stage Off.
7. Do not deploy unless latest exact-source SHA has 13 individually successful Tests jobs and 3 CodeQL jobs, with Issue #30 PII tail latency resolved and verified. Perform all 15 post-deploy live checks and have rollback ready.

## Evidence / regression

- `guardoath/tenant_record.py` defaults both missing attachment fields to Auto without a revision-dependent Off shim.
- `guardoath/attachment_ingress.py` requires mandatory Block for invalid probe and partial engine inspection.
- `tests/test_authenticated_attachment_ingress.py` checks legacy missing/explicit/mixed modes and observe+Flag+Off incomplete Block.
- Known failing PII job: [Tests 42228089099 / local-pii (3.13)](https://github.com/CollectiverseStudio/guardoath/actions/runs/42228089099/job/127926602314) — synthetic `privacy/date_of_birth1.input`, 560.611ms versus unchanged 500ms; dependent HTML report failure. Green reruns do not close the reliability defect.


## Read-only production legacy-mode inventory

After AWS access is available, run this **from the repository root**, using the existing read-only permissions or approved administrative profile. It does not change DynamoDB records or output any tenant identifiers or private record values:

```powershell
cd C:\Users\bellgj\Documents\Personal\collectiverse\guardoath
python -m tools.attachment_mode_audit --profile collectiverse-prod --region us-east-1 --table guardoath-tenants
```

The tool scans only the `record` attribute with strongly consistent reads and emits aggregate counts of `missing`, `auto`, `off`, and `invalid` for Input and Output; it also reports unparseable records and always reports `mutated_records: 0`. It needs the repository's existing optional boto3 AWS dependency and AWS permissions for `dynamodb:Scan`.

**Interpret the two review signals separately:** `requires_provenance_review=true` means at least one explicitly stored Off was seen and its intent needs revision/customer-history review. `requires_data_quality_review=true` means at least one stored record was unreadable or a mode value was invalid, so the audit cannot certify an error-free policy inventory; the command exits **2** in this case, rather than returning success for valid JSON containing invalid mode settings. Duplicate JSON object member names (including an attachment mode written twice, such as saved Off followed by Auto) are considered unreadable and require data-quality review; the audit must not silently accept the last value and declare the inventory clean. An unreadable record could contain an Off value that cannot be reliably counted, so `requires_provenance_review=false` is not conclusive while `requires_data_quality_review=true`. A command exit of **0** only means no invalid/unreadable mode records were detected: it does **not** clear any Off provenance finding. Review counts and both flags before proceeding. A scan with missing/malformed result pages, an omitted or inconsistent DynamoDB `Count` (required even on empty pages), or a repeated/invalid continuation cursor now aborts **without** asserting any totals: the tool emits a privacy-safe `audit_incomplete=true` and `requires_data_quality_review=true` report, with reason `scan_integrity_error`, and exits **2**. Do not interpret such an incomplete report as zero Off. A real empty DynamoDB page can omit Items only when Count is zero. The scan is read-only and strongly consistent per read, **not an atomic whole-table snapshot** under concurrent tenant writes; rerun during a controlled quiet period and review provenance before changing settings. Session/profile or client initialization failures also emit privacy-safe `audit_incomplete=true`, reason `aws_client_unavailable`, and exit **2**. AWS Scan transport, permission, or service failures (including failures after a page already returned) emit `scan_integrity_error` and exit **2** without traceback, cursor, tenant ID, or a fabricated partial count. These are incomplete audits, **not** evidence of zero saved Off values. All reports remain aggregate and privacy-safe; no customer settings are changed. It does **not** distinguish intentionally saved Off from system-materialized legacy Off; any nonzero Off count requires the separate provenance review described above. Test contract: `tests/test_attachment_mode_audit.py`. No automatic rewrite or credential disclosure.


## Strict stored JSON parsing

The read-only production mode audit deliberately rejects Python's permissive JSON
extensions `NaN`, `Infinity`, and `-Infinity` **even in unrelated nested tenant
fields**. These tokens are not valid JSON, and accepting them while counting an
attachment mode as Auto/Off could produce a misleading clean inventory of a
corrupt stored record. Duplicate keys and nonfinite tokens are both counted as
unparseable data requiring review; the audit never modifies such a record or
prints its content. A production inventory with any unparseable record exits
nonzero, and does not establish that no persisted legacy Off value exists.


A stored tenant record with **more than 128 nested JSON objects or arrays**
(including the root object) is rejected deterministically **before decoding**
regardless of Python interpreter recursion tolerance. An otherwise valid record
with 129 nested containers is counted as **unparseable**, requiring data-quality
review and exit code 2; quoted/escaped brackets inside strings do not count.
This fixes Python 3.14 accepting synthetic pathological nesting that Python
3.13 rejected. Decoder recursion errors are still separately caught. No partial
record contents or tenant identifiers are emitted. A safe empty scan must
not be inferred from the failure. The read-only tool never changes records.
