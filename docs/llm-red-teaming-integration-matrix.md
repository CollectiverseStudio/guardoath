# LLM Red Teaming: GuardOath integration and acceptance matrix

Source: https://apxml.com/courses/intro-llm-red-teaming (reviewed 2026-11-11).
The source is a six-chapter educational syllabus, **not** a tested GuardOath control specification. These are proposed implementation requirements derived from its topics. No control is marked implemented or effective solely because a similarly named rule exists.

## Architecture and ownership

- Tenant record remains the sole authority for enabled checks, stages (Input/Output), actions (Block/Flag/Allow/Redact/Off) and rule overrides. Existing mandatory attachment inspection failures remain Block regardless of observe mode.
- Apply structured deterministic validation to typed data and unstructured semantic validation to chat. Do not silently treat a semantic score as proof of malicious intent.
- Preserve the shared engine and API lifecycle: inbound validation, optional provider call, outbound validation, response. Tool/agent pre-execution inspection must be a distinct authorization gate, never implicit execution.
- Every control needs tenant configuration, API finding metadata, dashboard editing and explanations, positive/negative and benign look-alike fixtures, multi-rule regression, latency budget and release evidence. Findings must enumerate all applicable failures.
- Adversarial testing is authorized only against controlled models, mock endpoints and systems within explicit scope. Never test external systems without authorization.
- The current $29 Starter price is not an unlimited red-team testing entitlement; define quotas before enabling paid adversarial generation.

## Coverage matrix

| Source chapter / topics | GuardOath deliverable | Minimum acceptance evidence |
|---|---|---|
| 1. Foundations, lifecycle, roles, objectives, attacker mindset, legal boundaries | Scoped red-team campaign object: owner, target, authorization, exclusions, budget, start/end, threat model | Reject absent/expired authorization; audit scoped campaigns; documented responsible disclosure |
| 2. Direct prompt injection | Input instruction-hierarchy and role-spoofing checks | Block malicious override variants; allow quoted policy analysis and ordinary requests |
| 2. Indirect prompt injection | Provenance-aware checks on retrieved documents, web content, tool results and attachments | Untrusted content cannot issue privileged tool instructions; record source and trust boundary |
| 2. Training/fine-tuning data poisoning | Dataset provenance and offline evaluation workflow (not falsely claimed as an inference-time filter) | Quarantined poisoned fixture, lineage checks and post-training evaluation |
| 2. Evasion and obfuscation | Unicode normalization, encodings, homoglyphs, spacing and multilingual semantic variants | Equivalent attack variants caught without blanket rejection of legitimate multilingual text |
| 2. Jailbreaks and role-playing | Role-play, fictionalization, authority and multi-step coercion detection | Harmful and harmless role-play separated; benign creative writing allowed |
| 2. Sensitive information extraction | Secrets/PII and system-prompt disclosure checks on Input and Output | Known canaries never appear in allowed output; false-positive fixtures |
| 2. Denial of service/resource exhaustion | Input size, attachment size, token/cost budgets, concurrency and timeout limits | Over-budget request rejected before provider invocation; bounded latency |
| 2. Over-reliance and misinformation | Risk-graded uncertainty/evidence checks, not universal factual-truth guarantees | Unsupported claims flagged in designated high-risk workflows; citations not fabricated |
| 2. LLM APIs and interfaces | Auth, replay, malformed payload, session isolation, API-key scope, request provenance | Cross-tenant and unauthenticated calls denied; malformed envelopes blocked |
| 3. Manual adversarial prompts | Reproducible curated scenario library with expected actions | Per-scenario pass/fail and benign controls |
| 3. Automated generation and fuzzing | Deterministic mutation harness with fixed seeds and bounded campaigns | Hundreds/thousands of variants, reproducible results, strict budgets |
| 3. Open-source red-team tools | Evaluated optional offline adapters with license, security and data-leak review | No automatic external data upload; pinned versions and sandboxed execution |
| 3. Persona-based testing | Persona metadata in scenarios, not privileged role assignment | Multiple personas cannot override tenant policy |
| 3. Multi-turn attacks | Conversation-window evaluation and cumulative risk signals | Gradual escalation across turns detected; normal multi-turn support allowed |
| 3. Memory and context-window attacks | Conversation memory provenance, truncation disclosure and context isolation | Truncation cannot silently erase required safety constraints; cross-session leakage tests |
| 3. Bias and harmful content | Harmful-content and bias evaluation with balanced datasets | Measured precision/recall by risk class and legitimate look-alikes |
| 3. Semantic similarity evasion | Paraphrase, translation, typo and semantic-equivalence adversarial suite | Report evasion rate by family and benign false-positive rate |
| 4. Gradient-based and transfer attacks | Offline model robustness evaluation, not a claimed universal runtime defense | Reproducible authorized surrogate/target experiments and transfer rate |
| 4. Membership inference | Privacy leakage assessment and rate-limited query monitoring | Controlled canary tests; privacy review and risk reporting |
| 4. Model inversion / stealing | Query anomaly, extraction-pattern monitoring and provider rate limits | Bounded query budget; report without unsupported prevention claims |
| 4. Input-filter / output-sanitizer bypass | End-to-end bidirectional attack chain tests | Input, model/tool, output and redaction recheck all enforced |
| 4. Chained attacks | Multi-stage campaigns joining injection, tool abuse and exfiltration | Entire chain blocked at an enforceable stage; evidence per stage |
| 4. Black-box / low-resource attacks | Budgeted black-box test harness | Works without gradients; no uncontrolled provider spend |
| 4. Exfiltration simulation | Canary exfiltration to controlled sink only | Secret or unauthorized destination blocked, no real data transmitted |
| 5. Input validation / sanitization | Structured schema checks + semantic risk analysis | Deterministic/semantic paths and failure actions validated independently |
| 5. Output moderation | Output-stage confidential-data, harmful-content and policy checks | All applicable findings, redaction recheck and no unsafe release |
| 5. Adversarial training / instruction tuning | Documented external model-hardening recommendations (not GuardOath proxy functionality) | Separate optional offline model evaluation; no misleading dashboard control |
| 5. Monitoring and anomaly detection | Tenant-scoped risk telemetry, privacy-safe event summaries and trend alerts | No sensitive payloads in logs; isolation and retention tests |
| 5. Rate limiting / access controls | Per-tenant API, provider and red-team campaign limits | Exhaustion fail-closed, concurrency and usage tests |
| 5. Jailbreak detection / system defenses | Defense-in-depth policy composition and trust-boundary checks | Cross-rule, cross-stage regression and benign pass rate |
| 6. Reporting and findings | Campaign report: scenario, category, severity, evidence, expected/actual result | Export redacts secrets and includes reproducibility metadata |
| 6. Prioritization / remediation | Severity, exploitability, impact, owner, proposed mitigation and due date | Traceable remediation ticket and owner assignment |
| 6. Retesting / verifying fixes | Versioned regression baseline, replay and before/after comparison | Failed cases become permanent regression fixtures; no release without required pass |
| 6. Procedures and playbooks | Scoped runbooks, authorization, rollback, disclosure and audit records | Human-readable runbook and evidence retention policy |

## Initial deliverables and release gates

1. Inventory existing check/rule IDs and map every row above to **implemented / partial / missing** with source paths and tested evidence; do not count a keyword match as a passing control.
2. Add scenario manifest with stable IDs, threat boundary, stage, expected action, benign counterpart, tenant settings, and failure severity. Start with prompt injection, obfuscation, multi-turn escalation, exfiltration canaries and tool-call authorization.
3. Implement gaps in coherent batches through the existing engine, authoritative tenant record, customer dashboard, demo, API and user guide. Keep security-event explanation distinct from pre-execution authorization.
4. Run adversarial fuzzing at multiple scales, including original vs paraphrased/multilingual attacks; measure recall, false positives and 500-ms latency rather than raising timeout thresholds.
5. Gate production on exact-source full Tests, CodeQL, authenticated browser workflows, strict scenario evidence and 15 post-deployment checks. Publish failures promptly with failing job/test and release impact.

**Explicitly not yet complete:** No claim that all syllabus topics are implemented, no evidence that model training defenses can be performed by a runtime proxy, and no permission to execute red-team attacks against third-party services.
