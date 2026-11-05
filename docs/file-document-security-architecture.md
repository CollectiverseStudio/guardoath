# File & Document Security — unified tenant validation architecture

**Status:** CSV text-formula rule implemented and integrated into tenant settings; binary file inspection is **planned, not implemented**. This is the scope/acceptance design for extending the same GuardOath system to file types without adding separate dashboards, tenant stores or policy engines.

## Hierarchy and source of authority

- **Domain:** `file_security` — File & Document Security.
- **Controls:** `csv_formula` (implemented; accepts CSV *text*), future controls for content-type verification, Office files, PDFs, archives, and optionally CAD formats.
- **Rules:** individual independently configurable finding IDs, e.g. `csv_formula.potential_formula`; future rules must use stable IDs, positive and benign examples, provenance and privacy-safe reasons.
- **Tenant:** the existing `TenantRecord` owns independent input/output action fields and `input_rule_actions`/`output_rule_actions`. The current CSV control defaults Off and can be enabled for either stage; future controls must use this same record, revision-based save, isolation and enforcement.
- **Dashboard:** the existing check-and-rule table and Example Picker are the only configuration/demo surface. New controls appear through `/v1/controls`, not parallel forms or hard-coded alternate policy stores.
- **API:** `/v1/checks/input` and `/v1/checks/output` run the selected tenant rules. The current `CheckRequest` accepts `text` or `json` content, **not arbitrary file bytes**. Real binary-file inspection requires a separately authorized bounded envelope/ingestion contract before implementing DOC(X), XLS(X), PDF or archive parsers. Keep tenant identity, metering, request budget, fail-closed and privacy controls consistent with text checks.

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
