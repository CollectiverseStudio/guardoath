# AI Input Validation Framework — 11 families and executable acceptance backlog

**Status:** User-supplied requirements mapped to GuardOath, **not a claim of implemented or tested coverage**. This framework has 11 architectural families, 53 individual test scenarios and distinct trust boundaries. The original claim of “30–40 use cases” refers to conceptual groupings, not this full test inventory. The canonical 200-case catalog remains [UC001–UC200](use-case-manifest.json); IDs below are framework-specific, not UC IDs.

## Important implementation principles

- Input screening, output screening, data-ingestion validation, model-training defenses, and trusted tool execution are different controls; no single regex checker can provide all of them.
- Every case needs a positive fixture, a benign look-alike, an error/timeout fixture, an explicit stage, a host enforcement boundary, and a measured false-positive/false-negative evaluation before being marked verified.
- Do not reject a self-harm help request as malicious: provide support and crisis resources; block dangerous answer content where appropriate. Clinical safety requires expert review.
- Do not claim an image steganography, ultrasonic attack, adversarial-vision, or synthetic-voice detector without modality-specific evidence.
- For data scrubbing, never log raw sensitive values; validate transformations before allowing downstream use.

## 1. Prompt Security & Firewalling

**Primary target:** LLMs and chatbots.

| Test ID | Scenario | Stage | Safe acceptance fixture | Expected control | Current gap |
|---|---|---|---|---|---|
| 1.1.1 | System prompt leakage | Input | Probe direct override plus extraction request | Input signal and output leak check; no disclosure | Semantic detection not proven |
| 1.1.2 | Role-switching emulation | Input | Spoof developer or system role | Flag forged role boundaries | Lexical partial |
| 1.1.3 | Instruction boundary fuzzing | Input | Injected end-of-instructions delimiters | Preserve trusted message provenance | Requires host role provenance |
| 1.2.1 | Indirect injection in retrieved pages/email | Untrusted retrieval | External document attempts an account deletion | Never promote document text into tool authorization | Requires provenance and dispatch gate |
| 1.2.2 | Hidden HTML/Markdown commands | Document | Hidden or zero-size styled operational text | Inspect rendered and raw content | HTML parser/rendering needed |
| 1.3.1 | Persona/jailbreak hypothetical | Input | Roleplay requests unsafe operational guidance | Classify actual intent, not keyword alone | Semantic provider needed |
| 1.3.2 | Multilingual jailbreak | Input | Paraphrase attack in low-resource language | Evaluate language coverage and uncertain result | Requires multilingual evaluations |
| 1.4.1 | Base64 encoded instructions | Input | Encoded instruction string | Bounded decode then apply same checks | Partial labeled decoding |
| 1.4.2 | Leetspeak/cipher obfuscation | Input | Obfuscated bypass request | Normalize and classify without corrupting benign text | Not generally implemented |

## 2. PII & Data Privacy Scrubbing

**Primary target:** Enterprise data and APIs.

| Test ID | Scenario | Stage | Safe acceptance fixture | Expected control | Current gap |
|---|---|---|---|---|---|
| 2.1.1 | Luhn-valid payment card masking | Input and output | Synthetic test card | Redact only verified candidate; revalidate result | Local PII partial |
| 2.1.2 | Bank routing/IBAN validation | Input and output | Synthetic bank identifiers | Validate format/checksum; mask where policy requires | Pattern coverage varies |
| 2.2.1 | SSN and national IDs | Input and output | Synthetic government identifiers | Flag or redact by jurisdiction | Partial region coverage |
| 2.2.2 | PHI with contextual identity | Input and output | Synthetic name plus diagnosis | Flag sensitive linkage without exposing text | NER/context not assured |
| 2.3.1 | API key and token scanning | Input and output | Synthetic credential format | Block or redact secrets under policy | Secrets checker exists |
| 2.3.2 | Proprietary code signatures | Input and output | Synthetic internal symbol names | Match tenant-configured confidential signatures | Needs tenant policy |

## 3. Content Moderation & Safety

**Primary target:** Public-facing AI.

| Test ID | Scenario | Stage | Safe acceptance fixture | Expected control | Current gap |
|---|---|---|---|---|---|
| 3.1.1 | Explicit toxicity and slurs | Input and output | Non-actionable toxic-language fixture | Moderate with context and false-positive tests | Optional provider not calibrated |
| 3.1.2 | Nuanced harassment | Input and output | Context-dependent insult fixture | Flag harmful targeting without overblocking quotation | Semantic evaluation needed |
| 3.2.1 | Self-harm crisis | Input and output | Help-seeking crisis statement | Flag input, block harmful output, provide support | Human safety spec pending review |
| 3.2.2 | Violence/weapon instructions | Output | Request for actionable harm guidance | Block harmful operational answer | Human safety spec pending review |
| 3.3.1 | Adult material workplace rating | Input and output | Non-graphic adult-content fixture | Apply tenant-specific content policy | Policy-dependent |
| 3.3.2 | Graphic violence | Input and output | Non-graphic reference to violent imagery | Classify context, allow benign reporting | Provider evaluation needed |

## 4. Adversarial & Poisoning Defense

**Primary target:** ML and vision.

| Test ID | Scenario | Stage | Safe acceptance fixture | Expected control | Current gap |
|---|---|---|---|---|---|
| 4.1.1 | Adversarial pixel perturbation | Image | Synthetic perturbed image | Quarantine suspicious input, compare robustness | Not implemented |
| 4.1.2 | Spatial transformation robustness | Image | Rotate or crop test asset | Compare classifier stability; don't infer attack from rotation alone | Not implemented |
| 4.2.1 | Poisoned training labels | Training ingestion | Mismatched synthetic label | Quarantine outliers for review | Requires labeled reference data |
| 4.2.2 | Duplicate cluster poisoning | Training ingestion | Repeated synthetic records | Detect unusual concentration and provenance | Requires ingestion statistics |
| 4.3.1 | Membership-inference probing | Session telemetry | Repeated close decision-boundary queries | Apply privacy-aware rate/anomaly controls | Requires stateful telemetry |

## 5. Format & Token Budgeting

**Primary target:** Infrastructure and cost.

| Test ID | Scenario | Stage | Safe acceptance fixture | Expected control | Current gap |
|---|---|---|---|---|---|
| 5.1.1 | Context window overrun | Input | Synthetic over-budget prompt | Reject before model call using model-aware tokenizer | Character budgets exist; token count incomplete |
| 5.1.2 | Repeated-token flooding | Input | Large repeated benign string | Flag abusive repetition with calibrated threshold | Resource bounds partial |
| 5.2.1 | Malformed encoding/BOM | Raw bytes | Invalid UTF-8 fixture | Reject deterministically without crash | HTTP ingress dependent |
| 5.2.2 | Unicode direction overrides | Input | Bidi control test string | Flag controls without stripping legitimate joiners | Partial text security |
| 5.3.1 | Oversized JSON envelope | HTTP ingress | Valid but over-budget JSON | Reject before expensive parsing | Ingress budgets exist |

## 6. Semantic & Logical Sanity

**Primary target:** Workflow automation.

| Test ID | Scenario | Stage | Safe acceptance fixture | Expected control | Current gap |
|---|---|---|---|---|---|
| 6.1.1 | Future event-date anomaly | Structured input | Far-future claim date | Apply tenant-defined chronology window | Domain policy needed |
| 6.1.2 | Checkout before check-in | Structured input | Reversed synthetic dates | Block inconsistent ordering | Cross-field check exists |
| 6.2.1 | Impossible physical metric | Structured input | Implausible synthetic weight | Apply unit-aware domain range | Domain policy needed |
| 6.2.2 | Contradictory patient statements | Text | Inconsistent status and appointment | Flag for review; avoid clinical inference | Semantic detector absent |
| 6.3.1 | Out-of-scope domain request | Input | Bank assistant asked for recipe | Route or flag based on application scope | Tenant intent policy needed |

## 7. Structural Schema & Tool Gateways

**Primary target:** Agent tools and APIs.

| Test ID | Scenario | Stage | Safe acceptance fixture | Expected control | Current gap |
|---|---|---|---|---|---|
| 7.1.1 | Tool argument type mismatch | Proposed tool call | String supplied for integer field | Reject against schema before dispatch | Tool schema check exists |
| 7.1.2 | Missing authorization field | Proposed tool call | Missing required field | Reject and enforce authenticated identity independently | Schema alone insufficient |
| 7.2.1 | Unsafe interpreter tool request | Proposed tool call | Synthetic dangerous shell operation | Trusted sandbox and allowlist deny execution | OS sandbox outside checker |
| 7.2.2 | Generated SQL injection | Proposed tool call | Synthetic UNION-style injection | Parameterized SQL and least privilege at execution | Host enforcement needed |
| 7.3.1 | Unapproved outbound destination | Tool dispatch | External unapproved domain | Enforce egress allowlist after DNS and redirects | Host networking needed |

## 8. Multi-Modal Content Cleansing

**Primary target:** Image/audio/files.

| Test ID | Scenario | Stage | Safe acceptance fixture | Expected control | Current gap |
|---|---|---|---|---|---|
| 8.1.1 | EXIF metadata scripts | Image | Harmless EXIF script marker | Strip unneeded metadata and quarantine unsupported files | Not implemented |
| 8.1.2 | Image steganography indicators | Image | Synthetic LSB perturbation | Report anomaly, not definitive steganography | Not implemented |
| 8.2.1 | Inaudible audio command | Audio | Synthetic out-of-band audio signal | Band-limit where technically justified and test downstream ASR | Not implemented; frequency assumption varies |
| 8.2.2 | Synthetic voice spoofing | Audio | Consented synthetic speech | Flag suspected spoofing; require independent authentication | Not implemented |
| 8.3.1 | Image decompression bomb | Image | Extreme dimensions/aspect ratio | Reject before full decode by pixel/memory limits | Not implemented |

## 9. Grounding & RAG Veracity

**Primary target:** Retrieval and search.

| Test ID | Scenario | Stage | Safe acceptance fixture | Expected control | Current gap |
|---|---|---|---|---|---|
| 9.1.1 | Contradictory document chunks | RAG ingestion | Conflicting synthetic source text | Flag contradiction; retain provenance | Partial evidence checker |
| 9.1.2 | Unauthorized source folder | Retrieval | Document outside tenant permissions | Filter before retrieval and model exposure | Host authorization required |
| 9.2.1 | Contradictory source prices | RAG output | Two source prices differ | Report discrepancy and abstain if unresolved | Source-grounding partial |

## 10. Prompt Injection Canaries

**Primary target:** Exploit monitoring.

| Test ID | Scenario | Stage | Safe acceptance fixture | Expected control | Current gap |
|---|---|---|---|---|---|
| 10.1.1 | System prompt canary leakage | Input/output correlation | Synthetic unique secret canary | Detect unexpected canary exposure without returning secret | Not implemented |
| 10.1.2 | Document provenance watermark | RAG ingestion | Trusted document digest | Verify signed digest/provenance, not arbitrary marker | Integrity checker partial |

## 11. Anti-Bot & Intent Verification

**Primary target:** Availability.

| Test ID | Scenario | Stage | Safe acceptance fixture | Expected control | Current gap |
|---|---|---|---|---|---|
| 11.1.1 | High-velocity repeated prompts | Traffic telemetry | Repeated benign test prompts | Tenant-aware throttling; avoid blocking accessibility users | Quotas exist; semantic rate detection absent |
| 11.1.2 | Low-variance bot behavior | Session telemetry | Synthetic regular cadence | Risk-score behavior; don't infer identity from entropy alone | Not implemented |

## Engineering acceptance gate

1. Trace each framework test to a stable UC001–UC200 ID where genuinely equivalent; otherwise create a separate extension ID rather than forcing a false mapping.
2. Record `not_implemented`, `partial`, `verified` based on source, integration and CI evidence. Existing lexical patterns are partial signals, not semantic guarantees.
3. For input and output checks, verify both `mode="observe"` and `mode="enforce"` and the tenant's explicit rule action. A model-generated instruction is never proof of authorization.
4. For tools, verify schema, authenticated principal, tenant/resource ownership, approval, budgets, destination and sandbox controls at the trusted dispatch boundary.
5. For multi-modal data, bound raw bytes, decoded pixels/samples, metadata and computational work before expensive processing. Fail closed on unsupported formats.
6. Require 12 green CI jobs for the exact commit and 15 live checks for any production deployment, following [Director coordination](director-agent-coordination.md). AWS cost changes require a verified pricing table.

## Existing related documentation

- [161 lexical indicator explanations](security-indicator-use-cases.md)
- [Human safety scenarios](human-safety-scenarios.md)
- [Agentic safety acceptance](agentic-safety-acceptance.md)
- [Use-case coverage](use-case-coverage.md), [additional cases](additional-use-cases.md), [UC101–200](use-cases-101-200.md)

**Ownership:** The Director retains shared engine, tenant, hosted API, dashboard and deployment files. Atlas may implement isolated modules/tests and must coordinate registration. This framework is a requirements baseline and is not deployed.
