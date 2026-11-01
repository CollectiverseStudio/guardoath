# GuardOath authoritative tenant validation hierarchy

**Status:** Architecture contract approved in principle by Greg on 2026-11-01; integration changes pending Director-owned file review. This document does **not** claim that additional validation families are currently wired into hosted execution.

## Authoritative source of selection and action

For a tenant-authenticated operation, **the persisted tenant record is the authoritative configuration** specifying which validations are enabled and the finding action for each. The published validation catalog is descriptive metadata and implementation mapping, not a competing policy source. The engine compiles a bounded, versioned execution plan from one authenticated tenant-record snapshot.

1. Authenticate and load exactly one active tenant record from trusted storage. Verify identity, billing/limits, stage and authorized service boundaries independently of content controls.
2. Validate the record's schema, revision, entitlements and field settings; unknown or invalid enabled IDs must not silently execute or be treated as passes.
3. Build a deterministic execution plan from the tenant's **per-validation** fields (existing `check_<id>` pattern): `null` means disabled; `block`, `flag`, `allow` or, where supported, `redact` means enabled with that finding action. A check configured `allow` **still runs** and reports findings. Check settings remain on the record while disabled but do not affect execution.
4. Route each enabled validation only to compatible input, output, retrieval, proposed-tool-action, structured-security-event, image/audio/document or data-ingestion stages. An enabled rule that cannot run for the current stage is **not applicable**, not passed. If mandatory at that stage but unavailable, report **incomplete** and apply the configured safe failure policy.
5. Execute under aggregate deadline, provider budget and cost constraints. Preserve all findings, errors and source identities; deduplicate only verified aliases. Enforce independent authorization at trusted tool-dispatch boundaries immediately before side effects.
6. Combine the actual checker findings with the tenant's field-selected actions. Mandatory platform security/authorization controls cannot be disabled, overridden, or marked allowed by tenant content policy. Do not equate a tenant `allow` finding response with authorization to execute a privileged action.
7. Return a decision, each checker outcome, stage, tenant policy revision, and safe audit identifiers. Redaction requires a supported transform and revalidation before release. A timeout, unavailable provider or unsupported detector must not be represented as a clean pass.

## Canonical classification hierarchy

Each published validation has a stable unique identifier, immutable implementation version, stage and data-type compatibility, optional dependency and evidence requirements, and **one or more** catalog labels:
- Core content/security: prompt injection, lexical guards, secrets, PII/PHI, schema, resource limits.
- Human safety: crisis, medication/medical urgency, child protection, violence, exploitation, coercion. Requires context-aware stage behavior; safe supportive responses are not equivalent to punitive blocking.
- Agentic and trusted actions: tool invocation, permission checks, side-effect authorization, egress, resource ownership and approval evidence.
- Industry-specific controls: healthcare, finance, utilities, manufacturing and other 20 sectors; deterministic host-evidence invariants, not automatic blanket authorization.
- Specialized structured-event detection: SigmaHQ Windows process/PowerShell event rules; event-routing only, **never** silently applied to a chat prompt.
- Data integrity, retrieval and multimodal: RAG trust, provenance, ingestion, documents, audio/images and statistical checks where actually implemented.
Labels/packs organize and display controls, but only the tenant's authorized field selection schedules optional validations. A single detector may carry multiple labels without being run more than once.

## Tenant record example (illustrative, not current schema)

```json
{
  "tenant_id": "clinic_demo",
  "revision": 12,
  "default_profile": "tenant",
  "mode": "enforce",
  "failure_action": "block",
  "check_pii": "redact",
  "check_secrets": "block",
  "check_text_security": "flag",
  "check_human_safety_medication": "flag",
  "check_healthcare_prescription_action": "block",
  "check_agent_unauthorized_export": "block",
  "check_sigma_rule_9fbf5927_5261_4284_a71d_f681029ea574": null
}
```

The last four check fields are **proposed**, not current TenantRecord fields. Define stable ID normalization or a bounded serialized per-validation field mapping before implementation; do not assume arbitrary new keys currently validate. The initial check IDs and action support remain those in `guardoath/tenant_record.py` and `docs/tenant-records.md`.

## Potential authority conflict that must be resolved

The current hosted service permits selecting predefined example profiles through `policy_version` and `default_profile`; their actions can differ from the tenant's own `check_<id>` fields. This is not compatible with a strict reading of “the tenant record determines all validations and actions” unless the record stores/authorizes an immutable resolved profile snapshot or profiles are treated as templates copied into its fields. **Recommended:** preset profiles are initialization/edit templates, never runtime overrides of the tenant's per-check configuration. API-supplied validation codes may only narrow execution within entitled, tenant-enabled controls where safe; they may not turn off mandatory controls or enable checks configured off. Any proposed migration requires Director ownership and compatibility review; do not change hosted behavior silently.

## Scale and storage

Greg's requirement is a field for each validation. Preserve field-level independent action semantics, but evaluate storage constraints before modeling thousands of root-level DynamoDB attributes or fixed Pydantic fields. A typed bounded `validations` map on **the same tenant record** keyed by stable control ID retains one independent field per validation, supports additions without code-generation for every ID, and remains the single authoritative record. Explicitly benchmark DynamoDB item limits, API size, concurrency/versioned updates and audit costs. If flat `check_<id>` fields remain preferred, build migration and schema/version validation without changing authority.

## Non-negotiable acceptance tests

- Two tenants enable different subsets/actions for the same content; execution and decisions remain isolated.
- Disabled validation does not run; configured `allow` does run and reports a finding.
- Stages and content types route accurately; disabled/unavailable/not-applicable/incomplete/passed are distinct.
- Predefined profiles and request validation codes cannot override the stored per-validation action or enable a disabled check.
- Unknown IDs, malformed actions, stale revisions and missing required evidence fail in a controlled way.
- Trusted tool authorization is never bypassed by a configured `allow` action.
- Positive, benign, incomplete/timeout and transformation/revalidation cases are exercised for each integrated family.
- Industry and human safety group labels do not implicitly run every rule in a pack.
- Every response/audit entry identifies the effective tenant policy revision and applicable control version; usage/cost constraints apply.
- Production integration follows existing 12-job CI, Director exact-SHA release, and 15 live checks.

## Ownership / next steps

The Director owns changes to `guardoath/tenant_record.py`, `hosted.py`, `models.py`, `engine.py`, tenant storage, API, dashboards and deployment. Codex may develop isolated catalog/schema prototypes and regression contracts without modifying these files until explicit Director acknowledgement. Prioritize a migration-safe end-to-end slice over continuing to grow disconnected rule counts. Do not claim this architecture is fully implemented until verified.
