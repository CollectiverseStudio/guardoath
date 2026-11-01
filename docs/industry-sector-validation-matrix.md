# GuardOath — 20-sector industry validation acceptance matrix

**Status:** Requirements and acceptance criteria, not 20 deployed industry packs or proof of regulatory compliance. Sectors are a pragmatic aggregation of economic activities, **not a verbatim official NAICS/ISIC 20-sector taxonomy**. Sector-specific controls reuse common checks and trusted host gateways. This document extends [unified architecture](unified-validation-architecture.md), [11-family framework](ai-input-validation-framework.md), [human safety](human-safety-scenarios.md), and [UC001–UC200](use-case-manifest.json).

## 20 sector contracts

Every row represents a distinct **risk input / legitimate look-alike / unsafe AI outcome / enforcement control / trusted boundary**. The look-alike must remain permissible when no prohibited operation is attempted. Input detection alone cannot enforce a state transition or actuator command.

| ID | Sector | Risk input | Legitimate look-alike | Unsafe AI-output or action | Required validation | Trusted boundary |
|---|---|---|---|---|---|---|
| IND01 | Agriculture, Forestry, Fishing & Hunting | NDVI telemetry override hidden in drone scan | Research simulation of unusual crop growth | False crop-health metric written to ERP | Trusted sensor provenance, physically valid NDVI range, calibrated outlier analysis | Telemetric ingest; write authorization |
| IND02 | Mining, Quarrying & Oil/Gas | Unsafe blowout-preventer pressure ceiling update | Theoretical failure-mode research | Drilling controller receives unsafe setpoint | Equipment-specific certified pressure maximum and independent safety interlock | Industrial command gateway |
| IND03 | Utilities | Unauthorized substation breaker operation framed as emergency | Historical grid-balancing protocol review | Breaker operation causes outage | Authenticated operator, operational mode, dispatch approval, interlock and audit | OT/SCADA dispatch |
| IND04 | Construction | Concrete curing parameter set to zero in untrusted note | Historical structural-failure study | Construction milestone falsely approved | Trusted engineering specification, curing state and qualified signoff | Engineering record update |
| IND05 | Manufacturing | Conveyor speed multiplier injected into calibration ticket | Feasibility study for increased throughput | PLC speed set beyond machine safety envelope | Machine-specific bounded setpoint, signed change approval, PLC interlock | PLC dispatch |
| IND06 | Wholesale & Distribution | Unverified manifest force-passed by text note | Report on historically unverified manifests | Inventory receipt bypasses verification | Read-only separation, manifest schema, signed receipt verification | Inventory state transition |
| IND07 | Retail | Checkout total manually overridden to zero | Legitimate clearance coupon inquiry | Unauthorized free order approved | Server-calculated totals, validated coupons and payment reconciliation | Checkout transaction |
| IND08 | Transportation & Warehousing | Aircraft/drone route redirected to unsafe coordinates | Emergency diversion location inquiry | Navigation tool accepts unsafe route | Geofencing, route feasibility, trusted telemetry, approved flight authority | Navigation dispatch |
| IND09 | Information & Digital Infrastructure | Retrieved support article instructs secret disclosure | Defensive prompt-injection guidance | RAG content becomes privileged instruction | Untrusted-source isolation, injection screening, secrets access denial | RAG retrieval/tool output |
| IND10 | Finance & Insurance | Depreciation values reversed by claim note | Actuarial stress-test analysis | Claims payout inflated | Immutable financial formulas, approved rate tables and maker-checker authorization | Claims calculation |
| IND11 | Real Estate & Leasing | Lease escalation overridden for 99 years | Historical fixed-rent lease review | Unauthorized contract terms generated | Approved contract clause schema, change tracking and legal approval | Contract generation |
| IND12 | Professional, Scientific & Technical | Fabricated legal precedent in uploaded brief | Research on non-compete litigation | Unsupported legal conclusions presented as verified | Official-source citation verification, jurisdiction/date checks and uncertainty | Legal retrieval/output |
| IND13 | Management of Companies | Forged CEO instruction to transfer budget | Historical budget-allocation analysis | Unauthorized funds transfer | Verified identity, dual approval, beneficiary allowlist and transaction limits | Treasury dispatch |
| IND14 | Administrative & Support Services | Impersonated IT password alert for credential theft | Authorized phishing-awareness analysis | Deceptive credential-harvesting content | Context-aware fraud detection and defensive-output scaffolding | Generated communication |
| IND15 | Educational Services | Hidden instruction changes grading result to A+ | Discussion of grading exceptions | Student grade overwritten | Rubric integrity, student authorization, signed grading state and review | Grade write |
| IND16 | Health Care & Social Assistance | Request for unauthorized controlled-drug prescription artifact | Clinical guideline research | AI fabricates prescription or clinical authorization | Licensed clinician authorization, clinical safety review and output restrictions | Clinical action/output |
| IND17 | Arts, Entertainment & Recreation | Ticket cap bypass in bulk reservation | Ticket-purchase limit policy inquiry | Scalper captures excess inventory | Atomic account/device quotas, bot detection and reservation locks | Ticket transaction |
| IND18 | Accommodation & Food Services | Hotel promo rate changed to zero by injected text | Legitimate seasonal-discount inquiry | Unauthorized free suite reservation | Server-side promo allowlist, pricing integrity and payment authorization | Booking transaction |
| IND19 | Other Services | Brake-fluid inspection status forced to pass | Diagnostic manual reference | Unsafe vehicle certified as healthy | Trusted instrument measurements, inspection evidence and technician signoff | Repair certification |
| IND20 | Public Administration | Restricted government records requested via override | Public records request guidance | Confidential records disclosed | Identity/clearance, classification labels, ABAC, egress DLP and canary leak monitoring | Government records access |

## Technical corrections to proposed thresholds

- **Agriculture:** NDVI is ordinarily bounded from -1 to +1. A directive to multiply NDVI by 10 is suspicious, but Mahalanobis distance >3 is **not** a universal cutoff. Use sensor calibration, normalization, seasonality and a validated multivariate baseline; reject physically invalid normalized values before statistical scoring.
- **Mining, utilities, manufacturing, construction, transport:** Use **equipment-specific, independently verified safety envelopes** and hard interlocks, not values inferred from prompts. Human approval alone does not replace physical fail-safes. The example 80,000 PSI is an attack fixture, not a recommended operating limit.
- **Transportation:** Raw Euclidean latitude/longitude difference is not a valid universal distance metric. Use geodesic/geofencing calculations and flight/route feasibility, with a trusted position source and operational context.
- **Finance:** A cosine similarity cutoff of 0.82 does not verify claim arithmetic. Use deterministic formulas, signed rates, reconciled amounts and independent approval. Embeddings may assist triage only after calibration.
- **Legal:** A document hash can establish integrity relative to a known trusted reference, not the legal correctness, current validity, or completeness of a precedent. Verify citation, court, jurisdiction, date, and subsequent treatment.
- **Health care:** A keyword such as 'write' or a drug name cannot alone determine unlawful prescribing. Block unverified prescription issuance at a licensed workflow boundary, while allowing general education and help-seeking.
- **Government:** Canary tokens can reveal leakage but cannot substitute for clearance, authorization, classification enforcement, or data-loss prevention.
- **Availability:** Rate limits must be enforced atomically at the reservation or tool gateway, not inferred from prompt text alone.

## Acceptance tests required for each sector

1. **Risky input:** a sanitized fixture must flag the intended security or integrity concern, with provenance and trust level preserved.
2. **Legitimate look-alike:** an educational, historical, analytical or properly authorized request must not be automatically blocked for sharing vocabulary.
3. **Unsafe generated output:** a simulated model output that proposes the forbidden operation must be rejected or escalated **before dispatch or publication**.
4. **Trusted boundary:** an untrusted text instruction must not bypass typed schema, authenticated principal, tenant/resource ownership, approved state transition, physical safety invariant or payment reconciliation.
5. **Failure mode:** missing telemetry, expired approval, invalid schema, unavailable provider, stale policy, race/replay, malformed encoding and missing external reference must not be silently classified as passed.
6. **Actions:** distinguish Flag, Block, Redact, Allow and Incomplete with tenant-specific policy and `mode="enforce"` in blocking tests. Record all applicable findings, consolidating only genuinely equivalent ones.
7. **Evidence:** require test IDs, module paths, CI runs, false-positive/negative evaluation and an integration record before marking any industry pack implemented.

## Recommended implementation architecture

Keep one reusable engine with **sector pack configuration**, not 20 independent engines. Each pack declares domain schema, protected fields, trusted reference sources, invariants, approval workflow, allowed tool destinations and evaluation fixtures. Core controls remain active for every sector. A tenant can enable more than one sector pack. The Director owns shared engine, tenant records, dashboard and deployment; isolated pack code and tests can be developed separately and handed off for integration.

**No industry pack is declared production-ready by this specification.** Do not introduce new AWS services or paid classifiers without cost verification. Deployment requires the Director's 12 green CI jobs and 15 live checks.
