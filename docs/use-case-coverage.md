# Coverage of Greg's 50 example use cases

This inventory distinguishes actual implemented checks from partial signals,
optional provider contracts and planned features. It is not a claim that 50
production detectors exist. The detailed configuration/examples and limitations
are in [the user guide](user-guide.md).

Local checks are tested offline. Provider contracts are tested with synthetic
responses only; no live classifier calibration or paid service call has occurred.
A planned/unsupported check cannot legitimately be reported as passed. The group
catalogue and selected checker results expose disabled/incomplete/unimplemented
coverage. The ordinary conversation profile runs implemented local general-text
checks; application-specific checks require their configured contracts.

| # | Requested use case | Current coverage / gap |
| --- | --- | --- |
| 1 | Indirect injection in incoming email | Not implemented as document/email attack analysis; local formatting signals only. Optional Azure connector currently sends userPrompt, not documents. |
| 2 | Jailbreak detection | Optional input-only Azure user-attack connector; offline contract tested, live effectiveness uncalibrated. No local semantic jailbreak detector. |
| 3 | Invisible characters / homoglyphs | Selected invisible/bidi/control characters detected; no scrubbing or general homoglyph coverage. Legitimate joiners are excluded. |
| 4 | Base64/hex payload inspection | Explicitly labeled, one-layer, bounded text-security signals and opt-in secret inspection. Not recursive decoding or encoded-PII detection. |
| 5 | System-prompt extraction requests | Plain attack wording is deliberately demonstrated as a local coverage gap. Optional semantic service may signal attacks, not a prompt-leak guarantee. |
| 6 | Sandbox escape commands | Not implemented. Code execution and sandbox boundaries belong to the integrating application. |
| 7 | Prompt size / resource caps | Character/UTF-8 body/depth/field budgets, check quotas and concurrency controls. Not model-token counting or distributed billing quotas. |
| 8 | Token smuggling | Limited normalization for configured literal confidential phrases and bounded encoded secrets; no general adversarial token-smuggling detector. |
| 9 | Multi-turn manipulation | Not implemented; request_context is not analyzed by the local text detectors or sent by current connectors. |
| 10 | De-anonymization intent | Not implemented; identifier detection cannot infer correlation/de-anonymization intent. |
| 11 | Hate speech | Initial optional content_safety adapter supports provider hate signals on input/output. Offline contract tested, live classification uncalibrated. |
| 12 | Self-harm interception | No dedicated self-harm classifier or crisis-routing workflow implemented. Do not infer it from the initial five content categories. |
| 13 | Profanity masking | Not implemented. Current supported redaction is for selected sensitive identifiers, not profanity. |
| 14 | Brand political/religious neutrality | Not implemented; requires approved application-specific scope/rubric. Neutral identity examples are not automatically violations. |
| 15 | Extremism / radicalization | No dedicated classifier or reference database implemented. |
| 16 | Image CSAM detection | Not implemented; no image upload, perceptual hash database or vision adapter. |
| 17 | Cyberbullying | Optional provider insults/hate signals; no tested covert sarcasm, student-intent or repeated-harassment model. |
| 18 | Misinformation | Not implemented; no independent facts/evidence retrieval or verification service. |
| 19 | Plagiarism | Not implemented; no corpus/web index, provenance matching or originality claim. |
| 20 | Defamation | Not implemented; content moderation cannot establish truth or legal defamation. |
| 21 | SSN masking | Supported contextual U.S. SSN candidates and policy-controlled, revalidated redaction. No issuance/identity verification. |
| 22 | Card-number masking | Supported card patterns/Luhn and revalidated redaction. This alone does not establish PCI compliance. |
| 23 | PHI scrubbing | Selected labeled birth-date, medical-record and health-plan identifiers plus base PII patterns. Not full medical narrative/name/address/PHI detection. |
| 24 | Corporate secrets | Selected credential patterns, quoted assignments, labeled encoded secrets and configured literal confidential phrases. No semantic proprietary-document classification. |
| 25 | Geographic privacy consent | Not implemented; no user-location/consent/transfer-rules engine or compliance determination. |
| 26 | Biometric consent token | Not implemented; no biometric ingestion or cryptographic consent verifier. |
| 27 | Third-party copyright / IP | Not implemented; literal confidential phrases do not establish ownership, licensing or infringement. |
| 28 | Hashed log identities | JSON Schema can enforce a submitted field's shape; no hash provenance, safe-hashing or re-identification verification. |
| 29 | Financial pseudonymization | Selected labeled bank-account redaction. No reversible token vault or complete balance/name pseudonymization workflow. |
| 30 | Age-appropriate handling | Not implemented; no verified age metadata or reviewed per-age content rubric. |
| 31 | ID image legibility | Not implemented; no OCR/vision upload pipeline. |
| 32 | Invoice tampering | Not implemented; no forensic PDF/image analysis. |
| 33 | Receipt completeness | Schemas can check already extracted fields; no receipt OCR or source legibility verification. |
| 34 | Medical scan quality | Not implemented; no DICOM/image quality or clinical validation. |
| 35 | Deepfake detection | Not implemented; no image authenticity model or accuracy claim. |
| 36 | Handwriting legibility | Not implemented; no handwriting/OCR confidence pipeline. |
| 37 | Passport/MRZ consistency | Not implemented; no document ingestion, MRZ checksum or visual-zone comparison. |
| 38 | Document type / renamed files | JSON request media/encoding is checked. No uploaded-document classification, malware scanning or MIME-content forensics. |
| 39 | Signature presence | Not implemented; no visual signature detector or legal-execution workflow. |
| 40 | Image steganography | Not implemented; no pixel/file payload inspection or guarantee of hidden-payload absence. |
| 41 | E-commerce intent | Not implemented; schemas/required sections enforce form, not semantic shopping relevance. |
| 42 | Legal contract completeness | Configured labeled text sections can detect missing/duplicate/short bodies. No assessment of clause meaning, adequacy or legal sufficiency. |
| 43 | Symptom context | Schemas/sections can require submitted fields. No clinical interpretation, safe-triage or symptom-adequacy claim. |
| 44 | Code syntax | Source-code syntax is not implemented. The new proposed-tool allowlist/argument-schema checker is distinct from parsing or executing source code; no sandbox claim. |
| 45 | Real-estate photo rules | Not implemented; no image-type/advertisement/contact-detail vision review. |
| 46 | Sentiment escalation | Not implemented; no calibrated anger/frustration classifier or human-routing integration. |
| 47 | UI element layout | Valinator's own dashboard has mobile/browser layout tests. This is not a product endpoint for arbitrary screenshot/layout validation. |
| 48 | Financial fraud patterns | Submitted numeric/date comparisons and structural contracts only. No recipient ownership, bank-routing validation or fraud database. |
| 49 | Shipping barcode quality | Not implemented; no barcode/image inspection. |
| 50 | Academic references | Local citation IDs and exact quotations can now be checked against a policy-owned source catalog. No academic database/URL lookup, source authenticity or claim-truth/entailment verification. |

## Related original goal: bias and fairness

The existing bias checker remains an explicitly labelled synthetic test double.
A separate real local matched_outcomes checker now compares structured submitted
decisions under declared matching factors and attributes. Missing comparisons
are incomplete; different outcomes create review findings. It does not classify
free-text bias or establish demographic fairness. See [coverage](bias-coverage.md). A real evaluator
needs an approved use-case rubric, contextual evidence, uncertainty, reviewed
benign/positive examples and matched-scenario evaluation. A demographic mention,
a moderation finding or a schema pass is not by itself a fairness result.

## What completing the automated checks means

All runnable suites must pass for the implemented behavior, including benign,
positive and failure cases. That verifies those implementations and contracts;
it does not convert the planned rows above into working detectors. Independent
accuracy datasets, approved live services and specialized data/model pipelines
are separate acceptance requirements. Deployment is handled separately by Greg.
