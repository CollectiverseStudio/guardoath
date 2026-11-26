# GuardOath product roadmap

Recorded November 23, 2026 (America/New_York), from Greg's question,
“What type of things would we work on next either additions modifications I’m curious?”,
the proposed six-phase response, and Greg's direction to preserve and implement it.

This is the durable roadmap for that discussion. It preserves the full set of
ideas and their intended order. An unchecked item is a target to assess or
complete, not a statement that the capability is absent in every form, delivered,
tested, deployed, certified, or funded. Link actual implementation and acceptance
evidence before marking an item complete. There are no promised delivery dates.

## Product direction and immediate priority

Build an independent security control plane between an organization and its AI
ecosystem. Customers choose their AI provider, model and, when applicable, agent.
Ordinary applications and AI chat must work independently of agent execution.
Maintain one shared validation Engine and the existing authoritative tenant
record, API, dashboard and configuration wizard.

**Finish the owner admin console and production acceptance first.** The approved
implementation contract is [#39](https://github.com/CollectiverseStudio/guardoath/issues/39),
with authentication/read-only administration in
[#40](https://github.com/CollectiverseStudio/guardoath/issues/40), user/subscription/billing
operations in [#41](https://github.com/CollectiverseStudio/guardoath/issues/41), and
versioned category/check/rule publishing in
[#42](https://github.com/CollectiverseStudio/guardoath/issues/42).
These remain open until their actual acceptance criteria are met.

The concise [admin handoff](admin-console-handoff.md) records implementation and
exact-source evidence. The [financial-operation record](admin-financial-operations-design.md)
now covers source-enabled subscription/cap reconciliation and reviewed
period-end cancellation for CORE-04, following the deployed compatible reader
in 762e7b6. Runtime prerequisites remain explicit and visible in the owner
workspace. The next implemented slice adds reviewed full invoice refunds,
permanent per-charge claims, signed history and explicit provider-read-only
recovery. Its first release is a reader compatibility baseline with refund
writers source-disabled; separate writer activation and a dedicated refund
credential remain required. The record preserves the remaining credit,
plan-change and durable lifecycle/risk-event requirements. This does not mark CORE-04 delivered;
the handoff records exact-source release evidence and remaining live acceptance.
Keep all original targets and acceptance boundaries below until implementation
and verification establish completion.

For CORE-06, the [catalog publication record](admin-catalog-publication-design.md)
preserves actual full-registry forward/reversal app and Engine acceptance and
now records owner review/history, confirmed requests and the external
source-preparation/release worker. The retained complete lifecycle adds
bounded current-request selection, fixed-main remote publication, complete
exact-candidate CI, guarded deployment receipts, owner progress/recovery and
verified terminal control replacement. Release 762e7b6 enabled both catalog gates
and added in-memory operator configuration from the existing AWS setup after
all seven exact-source workflows/25 jobs passed. Its completed deployment
verified the exact backend and all 13 dashboard assets; d81db4d is its preserved
compatible rollback. These facts are recorded in the handoff.
An operator must invoke the worker;
browser requests do not provision or start a background service. General
category/check/rule authoring and live publication remain open; all original
targets below remain unchanged.

## Phase 1 — Complete the core commercial platform

| ID | Target | Type | Completion evidence |
| --- | --- | --- | --- |
| CORE-01 | Owner management of users, tenants, subscriptions, billing, costs, categories, checks and rules | Modification | #39–#42, authenticated browser/API workflows, persistent audit, real deployment |
| CORE-02 | Clearer customer dashboard configuration, navigation and reporting | Modification | Visible integrated controls and saved-state browser acceptance |
| CORE-03 | Configuration wizard guiding customers to appropriate safeguards | Enhancement | Real tenant revisioned setup, examples, errors and reconnect tests |
| CORE-04 | Subscription upgrades, downgrades, cancellations and usage monitoring | Enhancement | Provider-verified entitlements, replay/race/failure tests and audited actions |
| CORE-05 | Actual infrastructure costs, revenue and attributable tenant profitability | Addition | Verified sources, freshness, currency, estimated/delayed labels and explicit allocation limits |
| CORE-06 | Create, modify, version, test, review, publish and roll back rules | Enhancement | Stable IDs, signed audit, CI-bound releases, tenant Off preservation and rollback |
| CORE-07 | Production performance, availability, regression protection and recovery | Modification | Exact-SHA release CI, source parity, usable rollback and all 15 live checks |

- [ ] Complete CORE-01 through CORE-07 with evidence linked to each target.
- [ ] Complete customer registration, sign-in and verified subscription journeys tracked by [#36](https://github.com/CollectiverseStudio/guardoath/issues/36) and [#37](https://github.com/CollectiverseStudio/guardoath/issues/37).

## Phase 2 — Universal AI security gateway

Proposed flagship direction: a GuardOath endpoint through which application,
chatbot and agent messages flow. Tenant-configured inbound validation precedes
the selected AI model; outbound inspection, redaction, enforcement and audit
precede release of the response. Integrate with existing invocation work rather
than creating a parallel gateway or another source of tenant policy.

- [ ] GATE-01: OpenAI-compatible proxy endpoint.
- [ ] GATE-02: Adapters for Anthropic, Gemini, Amazon Bedrock, Azure and local models.
- [ ] GATE-03: Streaming response inspection with explicit buffering and release guarantees.
- [ ] GATE-04: Multi-turn conversation awareness.
- [ ] GATE-05: Employee AI usage protection for configured enterprise applications/chat.
- [ ] GATE-06: Provider-independent authentication and tenant policy enforcement.
- [ ] GATE-07: Centralized, privacy-preserving AI transaction auditing.

Applications need not use an agent. Gateway coverage applies only to traffic
actually routed through an integrated enforcement path; do not imply control
over unrelated employee AI accounts or unmediated actions.

## Phase 3 — Advanced AI security and human safety

| ID | Target | Purpose |
| --- | --- | --- |
| SAFE-01 | Conversational threat detection | Evaluate escalating patterns across messages |
| SAFE-02 | Employee safety | Identify configured potential self-harm, violence and serious-threat signals |
| SAFE-03 | Prompt injection protection | Protect application policy from hostile instructions |
| SAFE-04 | Data loss prevention | Enforce configured controls on confidential information disclosure |
| SAFE-05 | Document security | Inspect actual attachments for malicious instructions and sensitive data |
| SAFE-06 | AI output verification | Check defined business rules and independently trusted reference data |
| SAFE-07 | Behavioral anomaly detection | Identify suspicious changes in measured AI usage |
| SAFE-08 | Custom corporate policies | Express organization-specific acceptable AI usage |
| SAFE-09 | Incident escalation | Route configured incidents to authorized human reviewers |

- [ ] Deliver validated, documented behavior for SAFE-01 through SAFE-09.

Human safety is a proposed differentiator for employers, education and healthcare
use cases. Escalation must be configurable, privacy-conscious and reviewed by
appropriate humans. Document detection limits, false positives/negatives and
the boundary between a detected signal and an established incident. Do not infer
intent or a diagnosis from a classifier. Relate work to existing
[#33](https://github.com/CollectiverseStudio/guardoath/issues/33),
[#34](https://github.com/CollectiverseStudio/guardoath/issues/34) and
[#32](https://github.com/CollectiverseStudio/guardoath/issues/32).

## Phase 4 — Enterprise governance and compliance

- [ ] GOV-01: Reviewed control mappings for HIPAA, PCI DSS, GDPR, FedRAMP, NIST AI RMF and ISO 42001. Mappings are not certifications, legal conclusions or blanket compliance claims.
- [ ] GOV-02: Automated audit evidence showing which policies ran, when, under which source version, and their actual outcomes.
- [ ] GOV-03: AI asset inventory covering registered models, applications, agents, owners and risk classifications.
- [ ] GOV-04: Policy lifecycle management: draft, test, approve, publish and roll back without silently changing tenant choices.
- [ ] GOV-05: Executive reports for CISO, CIO, compliance and board audiences, with source-backed risk measures.
- [ ] GOV-06: Enterprise identity: SAML/OIDC SSO, SCIM provisioning and scoped delegated administration.

This phase expands the security platform into enterprise governance using the
same policy, identity, tenant and audit foundations. It does not replace the
current owner-only security boundary before scoped delegated roles are tested.

## Phase 5 — Optional AI agent security and execution control

An agent proposes an action; GuardOath validates its exact permissions,
parameters, risk and business policy; an integrated execution path then allows,
requires human approval or blocks the action. Preserve a durable receipt of the
actual mediated outcome. This remains optional for ordinary application/chat use.

- [ ] EXEC-01: Action Executor Registry of tenant-approved capabilities and permitted operations.
- [ ] EXEC-02: MCP and Lambda execution adapters.
- [ ] EXEC-03: Execution Gateway enforcing exact-action validation before invocation.
- [ ] EXEC-04: Receipts distinguishing approved, attempted, succeeded, failed and blocked actions.
- [ ] EXEC-05: MCP security, command/API/database/file-action inspection and restricted execution permissions.
- [ ] EXEC-06: Financial transaction limits and explicit human approval workflows.
- [ ] EXEC-07: Emergency stop for further mediated execution, with honest limits for already-running actions.

Use [#38](https://github.com/CollectiverseStudio/guardoath/issues/38) as the existing
execution-package workstream. GuardOath should be the source of record for calls
and actions it actually mediates, with customer-selected models and agents.

## Phase 6 — Commercial and integration expansion

| ID | Target | Intended value |
| --- | --- | --- |
| COMM-01 | Python, JavaScript, Java and .NET SDKs | Faster supported integration |
| COMM-02 | Security policy simulator | Understand policy effects before publication |
| COMM-03 | Observe-only mode where permitted | Assess impact while preserving mandatory hard blocks |
| COMM-04 | AI red-team testing | Evaluate defined adversarial security scenarios |
| COMM-05 | SIEM integrations, including Splunk and Sentinel | Connect evidence to existing security operations |
| COMM-06 | Private/isolated deployment | Support approved infrastructure and isolation needs |
| COMM-07 | Multi-tenant MSP console | Scoped service-provider management of multiple organizations |
| COMM-08 | AI cost optimization | Verified token usage, budgets and provider expenditure |
| COMM-09 | White-label licensing | Partner embedding and distribution |
| COMM-10 | Custom validation SDK | Controlled customer-developed rules with execution boundaries |
| COMM-11 | Security event investigation | Explain observed findings, policy decisions and possible remediation |

- [ ] Assess and deliver COMM-01 through COMM-11 against actual customer needs and integration evidence.

## Priority order preserved from the discussion

| Priority | Initiative | Dependency |
| ---: | --- | --- |
| 1 | Complete production readiness and owner administration | Current CORE requirements and release acceptance |
| 2 | Universal AI proxy endpoint | Reliable tenant/auth/billing/enforcement foundation |
| 3 | Multi-turn conversational safety | Defined conversation provenance and privacy boundaries |
| 4 | Customer policy simulator and testing | Shared engine and versioned policies |
| 5 | Enterprise security reporting | Trustworthy, persistent operational evidence |
| 6 | Provider adapters and SDKs | Stable gateway contract and bounded provider execution |
| 7 | Compliance mappings and audit evidence | Reviewed mappings and verified controls |
| 8 | AI agent execution gateway | #38 registry, approvals and exact-action receipts |
| 9 | Enterprise identity integration | Scoped role/tenant authorization model |
| 10 | Private deployment and partner licensing | Repeatable deployment and supported operating model |

Phases group related ideas; this priority table governs sequencing. Existing
work can satisfy a target after evidence review. A new integration does not
authorize a paid provider, infrastructure expansion or financial transaction.

## Possible product packaging

These names were proposals, not separate launched products or a request to
build three independent stacks:

- **GuardOath Protect:** input/output validation, privacy, human safety, security and data integrity.
- **GuardOath Govern:** governance, control mappings, risk, policy administration and audit evidence.
- **GuardOath Control:** agent authorization, execution inspection, approval and accountability.

The commercial direction is to combine AI security enforcement with enterprise
governance while remaining independent of a particular AI provider or cloud.

## Delivery and tracking rules

1. Read fresh [engineering coordination](director-agent-coordination.md) and its governing override, then `AGENTS.md`, current main/CI and active issue ownership before work. Use one source writer and non-forced main updates.
2. Finish the current admin contract before broadening into unrelated features. For each roadmap ID selected next, first inspect existing implementation and issue coverage, then record a bounded acceptance contract.
3. Integrate implementation with the existing tenant source of truth, shared Engine, authenticated API, dashboard and wizard where applicable. Backend-only code is not product completion.
4. Preserve customer safeguards: owner Cognito/MFA RBAC, signed persistent audit, provider-verified entitlement, explicit financial confirmation/idempotency, tenant isolation and revisions, saved Input/Output Off settings, host-signed reference authority, actual attachment bytes and hard Block on inspection failure.
5. Keep the original 500-ms PII deadline. No skipped/weakened tests, invented metrics, unapproved AWS/provider spend, or deployment/readiness/financial claims without evidence.
6. Complete positive, benign, adversarial, failure, replay, race and browser/API acceptance for the capability. Verify exact-SHA CI, rollback, source parity and [15 live acceptance checks](production-live-acceptance.md) for a release.
7. Update the target's evidence with the implementation commit, tests and deployment status. Commit documentation with substantive implementation where practical; avoid status-only commits.

### Current evidence ledger

| Workstream | Tracking | Status at roadmap capture |
| --- | --- | --- |
| Owner admin console | #39–#42 and [operator guide](admin-console-operator.md) | In progress; source features exist, complete live acceptance outstanding |
| Customer onboarding and subscriptions | #36–#37 | Acceptance remains open |
| Reliability, privacy and release | #30 and [live acceptance](production-live-acceptance.md) | Unresolved risk and live evidence must remain explicit |
| Conversation/human safety | #33–#34 | Existing partial implementation; future roadmap scope requires review |
| Attachments | #32 | Existing work; preserve failure-blocking and verify production |
| Optional action execution | #38 | Planned/partial foundations; exact current scope must be inspected before expansion |

No roadmap item is marked complete solely because it appears in this document.
