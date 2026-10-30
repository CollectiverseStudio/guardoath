# Input-validation architecture expansion — competitive research follow-up

Status: **approved roadmap and acceptance design, not implemented feature claims**. Decision date: 2026-10-29. Owner: GuardOath. The customer has approved adding the recommendations to the product plan; this does not certify detector accuracy, grant permission to weaken controls, or authorize external paid services.

## Source references and evidence caution

These public pages motivated the design. They are **design inputs, not verified independent benchmarks**:

1. Nightfall AI — https://www.nightfall.ai/ai-security-101/model-input-validation
2. SnailSploit — https://snailsploit.com/ai-security/wiki/defenses/input-validation/
3. AWS Prescriptive Guidance — https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-security/best-practices-input-validation.html
4. VibeProd — https://www.vibeprod.ai/blog/input-validation-in-ai-code-a-developers-guide
5. Cisco AI Defense — https://www.cisco.com/site/us/en/products/security/ai-defense/ai-model-application-validation/index.html
6. ApX — https://apxml.com/courses/intro-llm-red-teaming/chapter-5-defenses-mitigation-strategies-llms/input-validation-sanitization-llms

## Architectural decision

GuardOath should validate **distinct trust-boundary surfaces**: (a) direct user input, (b) retrieved/RAG documents, (c) tool/API responses, (d) proposed tool arguments, and (e) generated output. Every assessment carries authenticated tenant/application identity, stage/surface, provenance, policy and pack versions, destination, and evidence identifiers. Content from a lower-trust surface must never be promoted to system/developer instructions. The **host must enforce** allow, transform, review, or block at the actual AI ingress or tool dispatch boundary.

Processing order (conceptual; do not assume these steps all exist in code):

1. Authenticated tenant admission, size/rate limits, privacy/egress routing.
2. Mandatory CORE deterministic checks and selected industry packs; preserve independently evaluable findings.
3. Optional approved semantic classifiers for indirect injection, role spoofing, task hijacking, and policy drift; no silent external PHI transmission.
4. Versioned tenant policy aggregation: all findings, incomplete/unsupported checks, enforcement action, and exact override eligibility.
5. Authorized redact/transform, revalidate the transformed content, or review a tightly scoped exception; no unconditional override flag.
6. Host-side execution/ingress enforcement and redacted audit/usage event emission.
7. Separate offline continuous evaluations of security efficacy, false positives, latency, and provider cost.

## Seven prioritized workstreams

| ID | Priority | Capability | Existing foundation (not completion) | Required new work | Acceptance gate |
|---|---|---|---|---|---|
| IV-01 | P0 | Semantic prompt-injection and task-boundary assessment | text-security formatting/encoded signals, optional provider contracts | Versioned semantic provider interface, reviewed benign/adversarial dataset, tenant opt-in and privacy-safe routing | Measure attack recall, false positives, unknown/incomplete outcomes and provider failures; no claims of universal jailbreak detection |
| IV-02 | P0 | RAG and third-party tool-response quarantine | JSON ingress, text/security checks, proposed-tool schema validation | Provenance-carrying untrusted-content envelope, retrieved-document parsing, response scanning and explicit instruction/data boundary | Fake system/tool messages remain data; benign quoted content passes; encoded injection and parser errors fail safely |
| IV-03 | P0 | Continuous efficacy regression | Saved eval suites and CI | Baseline metrics per checker/policy/provider, fixed benign/attack holdouts, drift/regression dashboards and signed result artifacts | Report denominators, false-positive rate, recall, bypass rate, latency and cost; distinguish synthetic fixtures from live validation |
| IV-04 | P1 | Language-aware source-code/AST validation | JSON Schema and tool-argument contracts | Language-specific parser adapters and curated SAST rules, with sandboxing and resource caps | Valid code, invalid syntax, dangerous construct and parser timeout cases; do not equate syntax pass with safe code |
| IV-05 | P1 | Tenant-specific adaptive policies | tenant registry, optional packs, override-policy primitives | Persistent per-tenant versions/roles, explicit pack entitlement, reviewed threshold changes and policy rollback | Tenant A settings never affect B; no client-supplied hard-rule disable; policy drift invalidates exceptions |
| IV-06 | P1 | Safe transform and revalidation | identifier redaction concepts and policy actions | Approved transformation registry, semantic-preservation checks where possible, second-pass validation and original-content retention controls | Original is never sent to an unapproved provider; transformed content is rechecked; failed transformation blocks |
| IV-07 | P2 | Security posture and assessment reporting | audit manifest, saved evals, checker result metadata | Coverage and efficacy reports tied to source commit, model/provider version, policy and datasets | Explicitly distinguish implemented/verified/partial/proposed; no compliance certification without external evidence |

**UC relationships:** IV-01 → UC001, UC002, UC005, UC009, UC051, UC057. IV-02 → UC001, UC106, UC107. IV-04 → UC044, UC083. IV-05/06 cut across CORE and industry packs. IV-03/07 are cross-cutting assurance, not additional detectors. UC101–200 stay `proposed` pending separately approved checker implementations and evidence review.

## Multi-tenant override rules

- Tenant administrator sets eligible finding IDs, severity ceilings, permitted destinations, review roles, retention and allowed packs. Default is **no exception**.
- A false positive may be reviewed, but an end-user acknowledgement does not authorize PHI disclosure or override authentication, tenant isolation, tool permissions, resource ceilings, or prohibited provider routing.
- Bind any single-use grant to tenant, app, actor, content digest, finding IDs, policy/pack versions, destination, purpose and expiration; consume atomically.
- Always rerun CORE and every non-exempt required check. An incomplete required assessment cannot be overridden as clean.
- Return **all independently evaluable findings** (bounded response size) and explicit unexecuted/incomplete checks, not merely the first error.
- Stripe or a partner such as Tokatlas may be the payer; neither billing mode may alter mandatory enforcement. Record meter events idempotently without storing raw sensitive payloads.

## Efficacy and adversarial test matrix (to implement, not existing evidence)

For each workstream and stage, require: positive malicious/invalid sample; benign lookalike; mixed content; Unicode/encoding; malformed input; large payload/timeout; missing provider; cross-tenant request; policy change; override replay; and actual downstream-denial test where relevant. Track expected finding IDs and actions in versioned fixtures. Record test IDs and exact functions in the use-case manifest only after implementation and successful CI.

**Metrics:** TP/FP/TN/FN with denominators and confidence intervals where meaningful; false-positive rate FP/(FP+TN); recall TP/(TP+FN); end-to-end bypass rate on a fixed adversarial corpus; P50/P95/P99 latency; bytes sent to external providers; per-tenant metered units and spend. Do not compute detection accuracy from a suite that tests only schemas or mocked provider contracts.

## Execution order and ownership constraints

1. **First:** restore green CI for existing baseline, including the reserved configuration dependency-isolation and dashboard tests; do not weaken tests.
2. Finish the UC001–100 non-privacy evidence audit and run the prepared 94 API scenarios against real GuardOath. Document each failure.
3. Complete UC021/023/029/037/048 only after commit B is confirmed and reconcile the changed duplicate-finding behavior.
4. Build IV-03 evaluation reporting and IV-02 trusted surface envelopes as separately approved, scoped tasks. Before touching engine/models/app or reserved files, coordinate with their owner.
5. Implement IV-01 semantic provider with opt-in, egress restrictions, cost budgets, and calibrated fixtures; follow with IV-05/06, IV-04 and IV-07.
6. Do not expand the UC101–200 checker inventory merely to increase counts. Rank feasibility and record required adapters first.

### File ownership and status

This roadmap is a new documentation file within Atlas's permitted new-file scope. Existing `guardoath/app.py`, `guardoath/models.py`, `guardoath/engine.py`, privacy components, and the other agent's reserved files are **not** modified. No paid provider calls, new detectors, live benchmarking, or billing changes are performed by this document.

### Definition of done for each future feature

Implementation and host-enforcement evidence; exact unit/API/eval test IDs; negative and benign cases; error/incomplete behavior; tenant isolation; data privacy review; performance and cost baseline; user-guide and manifest updates; and passing CI on the relevant commit. Until then label the feature `proposed` or `partial` as appropriate.
