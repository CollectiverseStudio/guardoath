# File & Document Security — unified tenant validation architecture

**Status:** CSV text-formula rule implemented and integrated into tenant settings; binary file inspection is **planned, not implemented**. This is the scope/acceptance design for extending the same GuardOath system to file types without adding separate dashboards, tenant stores or policy engines.

## Hierarchy and source of authority

- **Domain:** `file_security` — File & Document Security.
- **Controls:** `csv_formula` (implemented; accepts CSV *text*), future controls for content-type verification, Office files, PDFs, archives, and optionally CAD formats.
- **Rules:** individual independently configurable finding IDs, e.g. `csv_formula.potential_formula`; future rules must use stable IDs, positive and benign examples, provenance and privacy-safe reasons.
- **Tenant:** the existing `TenantRecord` owns independent input/output action fields and `input_rule_actions`/`output_rule_actions`. The current CSV control defaults Off and can be enabled for either stage; future controls must use this same record, revision-based save, isolation and enforcement.
- **Dashboard:** the existing check-and-rule table and Example Picker are the only configuration/demo surface. New controls appear through `/v1/controls`, not parallel forms or hard-coded alternate policy stores.
- **API:** `/v1/checks/input` and `/v1/checks/output` run the selected tenant rules. The current `CheckRequest` accepts `text` or `json` content, **not arbitrary file bytes**. Real binary-file inspection requires a separately authorized bounded envelope/ingestion contract before implementing DOC(X), XLS(X), PDF or archive parsers. Keep tenant identity, metering, request budget, fail-closed and privacy controls consistent with text checks.

## Greg-approved attachment-triggered default activation (2026-11-05)

**Product requirement:** File & Document Security is contextual, not a permanent all-message CSV check. **Without any attachment**, the file-specific checks are not selected and must not turn an ordinary text chat into a CSV scan. **With an attachment**, the engine must automatically select the applicable, *implemented* format-specific security controls for that attachment, subject to the tenant's explicit per-control/per-rule policy. Each attachment is inspected independently and the response preserves all findings, with strongest configured action and fail-closed on required inspection failures.

**Activation decision, in order:**
1. A trusted API ingestion layer determines whether bytes actually arrived and classifies the file by content evidence. User-supplied attachment names, MIME values, `request_context`, prompt text saying "attached", or arbitrary chat strings cannot activate or bypass the file controls.
2. On **no attachments**, file controls are **not applicable/off** to the request, even if a tenant has enabled auto-selection for supported attachments. Existing ordinary text checks still run.
3. On **one or more attachments**, apply a tenant-record default mode of `auto` for supported file controls; choose the applicable check(s) for each file type. This means CSV formula scanning automatically applies to a CSV attachment when the ingestion path really supplies its parsed CSV text. It does **not** run on a DOCX/PDF just because a file is present.
4. A tenant may explicitly set a supported file check or an individual rule **Off**, and that explicit override takes precedence over the auto default. A configured Block/Flag/Allow remains authoritative for a selected rule; do not silently force a tenant-selected Allow to Block or edit persistent settings because an attachment arrives.
5. If an attachment is unsupported, encrypted without a safe parser, truncated, malformed or exceeds a trusted budget, do **not** return "passed" or route unchecked document contents to the AI. Record an `unsupported` or `incomplete` outcome and apply the tenant's configured fail-closed behavior and release policy. Do not claim inspection succeeded.
6. Detaching the file or sending the next message without files returns file-specific controls to **not applicable**, without changing the tenant's saved preferences. Multiple attachments preserve per-file results; do not stop after the first finding.

**Tenant/API acceptance design:** The current `input_check_csv_formula`/`output_check_csv_formula` fields are `Action | None`, with `None` meaning Off for *all* messages; they do **not yet implement attachment-triggered `auto`**. The current `CheckRequest` supports only text/JSON and has no trusted file attachments. Add a **backward-compatible, explicit** tenant setting (or typed per-control policy mode) that distinguishes `auto` from `off` without silently rewriting existing tenants. Add a bounded authenticated attachment representation and trusted server-side detection before implementing activation. Avoid treating file mentions as attachments or using an unsupported file extension as proof that inspection ran. Initial default on *new* tenant records may be `auto`, but migration of stored records must preserve prior explicit Off actions; document rollout/migration and test it. Any attachment parser must remain bounded and non-executing.

**Dashboard acceptance design:** In the existing check/rule table, display **Auto (when matching attachment exists)** separately from Off/Block/Flag/Allow, show saved versus per-message effective actions, and offer a demo with an actual trusted synthetic CSV attachment (not text masquerading as a file). When no attachment exists, explain "not applicable — no attachment"; for a matching CSV attachment show selected checks, findings and whether the tenant overrode defaults. Do not introduce a second file-policy dashboard.

**Required regression matrix:** 0 files, 1 CSV, 1 DOCX unsupported, CSV+DOCX, multiple CSV, wrong extension/MIME, quoted "attachment" in the prompt without bytes, tenant explicit Off, stage-specific overrides, disabled individual rule, malformed/overbudget/truncated file, tenant isolation, stored-record migrations, result no-echo and no-bypass to AI on incomplete required inspections. Verify exact-HEAD CI and subsequent authorized 15/15 live checks; these capabilities are **not currently implemented or deployed**.

## Initial proposed rules and format coverage

| Control area | Proposed rules/use cases | Status |
|---|---|---|
| CSV/TSV | CSV formula injection; TSV formula injection; dialect/row-shape confusion; escaped-CSV look-alikes | `csv_formula.potential_formula` implemented for CSV text only; other cases backlog |
| Shared file ingress | Magic-byte/claimed type mismatch; unsupported type; oversized file; invalid UTF-8; archive/decompression bombs; duplicate filename/path; safe metadata | Planned; no binary ingress route |
| Word DOC/DOCX/DOCM | Embedded macros/active content; external template relationships; hidden text; linked resources and document prompt injection | Planned, no parser or detector |
| Excel XLS/XLSX/XLSM | Spreadsheet formula risk; embedded macros/objects; DDE/external links; hidden sheets; risky workbook relationships | Planned, no parser or detector |
| PDF | PDF JavaScript/actions; launch actions and attachments; dangerous external links; suspicious hidden text | Planned, no parser or detector |
| ZIP and other archives | Path traversal, symlinks, excessive decompression, nested archives, executable attachments | Planned, no archive extraction |
| CAD (if requested) | Referenced external content, embedded scripts/macros, unsupported binary formats | Candidate; confirm requested CAD types first |

A suspicious document *indicator* is not a verified exploit or malware verdict. Do not execute office macros, spreadsheet formulas, PDF actions or scripts to inspect content.

## Non-negotiable shared file-ingress contract

1. **Content identity:** trusted server-computed content hash, size, detected media signature, claimed MIME, extension and selected format; never trust extension or user-provided MIME alone. Do not use source file names in findings/logs.
2. **Bounded parsing:** per-tenant input size, expanded size, nesting/decompression ratios, processing deadline, total objects and bytes inspected; no unbounded parsing or untrusted network fetches. Unsupported or ambiguous file formats must return `incomplete` with the configured fail-closed action, not `passed`.
3. **No execution:** sandbox parsers where needed; prohibit scripts/macros/formulas, external entity resolution, link following, shell execution and access to host filesystem outside temporary guarded inspection.
4. **Privacy:** all file fixtures synthetic; report stable rule IDs and generic explanations only. Store no raw content in analytics, logs or public eval output. Preserve necessary tenant isolation and opt-in for any provider-backed scanning.
5. **No duplicate engines:** file-type controls run through the existing tenant-record action/failure machinery and the existing dashboard Example Picker and check-result presentation. Any new upload/bytes endpoint must integrate with those same policies, usage accounting and access control.
6. **Stage independence:** configurable Input/Output and per-rule Off/Allow/Flag/Block. A rule that cannot inspect its required format is inapplicable only if it is not selected; an enabled required checker must never silently treat unparseable input as clean.
7. **Cost and deployment:** prefer bounded local standard-library parsing and static fixtures. New third-party parsers, paid AV services, storage, Lambda sizing or hosting must have a reviewed cost/security impact and explicit authorization before deployment.

## Minimum acceptance before claiming a format implemented

- A document actually reaches a safe parser via an authenticated tenant-bound API contract, not merely an example text string.
- Tests for one true positive, a legitimate look-alike, malformed/unsupported files, size/budget exhaustion, encrypted/complex variants as applicable, and missing/ambiguous format.
- Multi-rule accumulation and strongest-action tests; exact stable finding IDs, no raw content in findings, cross-tenant isolation and stage-specific outcomes.
- One record per tenant is authoritative; save/reset with revision conflict; dashboard tables, user-guide anchor, example picker and public control registry all reflect the same settings.
- CI at the exact implementation SHA passes required Tests and CodeQL; explicit production authorization and live verification separately established. A green unit test does not equal deployed file-inspection efficacy.

## Integration path

1. Keep existing CSV text checker functional. Introduce `file_security` as the domain for its existing tenant control, without adding a new control for an already implemented rule.
2. Expand source-only positive/benign and tenant/dashboard acceptance; preserve CSV Off defaults and format-warning semantics.
3. Design and approve the common binary file envelope/API and low-cost parser isolation. **Do not add binary parsing or upload claims before implementation and authorization.**
4. Add real format controls in complete batches, prioritizing XLSX and DOCX once common ingress is available, then PDF and archives. Treat legacy binary DOC/XLS separately from OpenXML; don't assume a single parser supports both.
