# GuardOath — Proposed document, OCR and image validation architecture

**Status:** PROPOSAL ONLY — no OCR, document-forensics, image, DICOM or deepfake detection is implemented by this document.  
**Owner:** Atlas proposes; Director reviews integration/API/UI ownership; Greg approves new checker implementation and paid services.  
**Date:** 2026-10-30

## Motivation and use-case mapping

The current GuardOath check API accepts bounded UTF-8 JSON; its existing media-ingress tests deliberately reject direct PNG, JPEG, WebP, PDF, DICOM and multipart requests. Existing JSON Schema checks can validate already-extracted fields. These are not OCR or image forensics.

| UC | Requested outcome | Proposed first implementation | Limitations |
|---|---|---|---|
| UC031 | ID image legibility | Local image decode, dimensions, orientation, blur/glare/contrast signals, OCR confidence | Does not prove an ID is authentic or valid |
| UC032 | Invoice tampering | Extract PDF metadata and text, compare OCR/text layers, record modification/signature indicators, flag suspicious inconsistencies | Metadata and pixel heuristics cannot establish fraud |
| UC033 | Receipt completeness | OCR receipt to structured candidate fields, then reuse existing JSON Schema for merchant/date/total | OCR errors and missing fields need explicit human review |
| UC034 | Medical scan quality | Separate optional DICOM adapter for basic format/dimensions/metadata checks | Clinical image-quality and diagnostic adequacy require specialist validation |
| UC035 | Deepfake detection | Optional external provider contract with versioned evidence and thresholds | Probabilistic result, no guarantee of authenticity |
| UC036 | Handwriting legibility | OCR confidence, script support, region segmentation and low-confidence review | Handwriting accuracy varies significantly by language and capture quality |

## Proposed staged architecture

1. **Separate ingestion boundary**: add a new authenticated, tenant-scoped document endpoint (not the existing JSON-only `/v1/checks/*`). Director must own or approve `guardoath/app.py`, hosted metering and tenant changes. Require MIME allowlist plus magic-byte validation; limit bytes, pages, pixel count, decompression ratio, time and concurrency; reject encrypted or malformed files without leaking content.
2. **Isolated extraction worker**: no network or executable macros, restricted filesystem, no privileged tools, resource limits, sandboxed parsers. Extract native PDF text first; OCR only raster/image regions. Use optional locally installed OCR (for example Tesseract) behind an adapter, not as a mandatory core dependency.
3. **Normalized intermediate result**: `DocumentAnalysis` with type, pages, extracted text, field candidates, bounding boxes, per-field confidence, provenance/processor version, parse errors and review status. Keep raw bytes out of logs, responses and persistent storage by default. Apply retention/deletion policies.
4. **Existing validation reuse**: feed explicitly extracted JSON/text to current GuardOath checkers with clear `extraction_unverified` status, rather than treating OCR output as trusted original content. Distinguish parsing failure, OCR uncertainty, policy findings and user overrides.
5. **Optional forensic modules**: PDF signature validation, metadata anomaly checks, image recompression/region consistency heuristics and optional specialized authenticity providers. A valid signature proves signed bytes under a verified chain, not truth of document contents; heuristics are not definitive tamper detection.
6. **Policy/tenant enforcement**: per-tenant feature permissions, fail-closed behavior when a required extractor is missing, audit events, cost budgets and optional paid-provider opt-in. Never silently bypass content validation or count rejected unauthenticated uploads as successful checks.

## MVP proposal and test plan

**Phase A (local, no paid providers):** build a pure offline document-result schema, a standalone file-type/magic-byte validator, strict size/pixel/page budget helpers, and a mock OCR adapter contract. Do not alter existing API, CI or Director-owned files without coordination. Unit tests: benign/invalid PDF and image fixtures, polyglot and extension spoofing, corrupt files, oversized decompression, empty/no-text documents, multilingual/handwriting uncertainty, malicious metadata strings, no raw-data echo, deterministic timeouts and stage-specific policy decisions.

**Phase B (Director-approved integration):** new upload endpoint, isolated worker, tenant permissions, usage accounting, dashboard UI, end-to-end API/browser tests, accessibility and rate limiting.

**Phase C (optional, subject to cost/security approval):** cloud OCR, document intelligence, DICOM quality modules and image-authenticity adapters. Each needs independent labeled accuracy evaluation, false-positive reporting and provider contract tests before a supported status claim.

## Acceptance criteria

- Original input bytes are never automatically interpreted as safe or trustworthy.
- Unsupported formats and extractor failures are explicit incomplete/error results, never silent passes.
- The response distinguishes **extracted**, **validated**, **review required**, and **not assessed**.
- OCR confidence is not equated with authenticity, legal validity, or medical safety.
- Every new detector has positive, benign/lookalike, adversarial and error/budget test cases.
- No claim of deepfake detection, invoice fraud detection, medical diagnosis, ID authenticity or compliance certification without independently validated evidence.
- No unapproved paid API calls, deployments, persistent PHI storage or edits to Director-owned API/dashboard code.

## Coordination decision requested

**Director:** Please review the proposed interface and identify integration constraints, planned API/tenant/media work, file ownership and any overlap. Atlas proposes to start only Phase A in new `guardoath/document_analysis_*.py` and `tests/test_document_analysis_*.py` files after explicit approval. Greg's earlier handoff requires approval before implementing new checkers; this proposal is not approval. The Director remains owner of hosted API, UI, tenant management and deployment.

**Greg:** Approve or adjust Phase A before any new checker or extraction subsystem is implemented.
