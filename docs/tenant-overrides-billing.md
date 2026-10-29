# Multi-tenant overrides and billing integration

## Isolation model
A tenant is a customer organization, not merely an API key. Every application credential maps to one tenant and one application; trusted policy, optional packs, override entitlements, usage and evidence must be scoped to both. Cross-tenant access fails closed. The existing API still authenticates application IDs only; a persistent tenant identity database is **not yet implemented**.

## Input override protocol (target)
1. Input check returns all completed findings and an overall decision. Incomplete assessments are never treated as clean.
2. Tenant policy classifies exact rule IDs as non-overridable, user-acknowledgeable or reviewer-required. Missing policy defaults to no overrides.
3. A resubmission references the prior check and selected finding IDs. The server authenticates the actor and, where needed, an independent reviewer. It verifies immutable content digest, tenant, application, policy version, pack versions, destination/provider and purpose.
4. Server issues a short-lived, single-use exception only for approved findings, persists a hashed nonce with atomic consumption, reruns CORE and all other required checks, and denies new findings or policy drift.
5. Actual downstream AI ingress must enforce the decision. No general-purpose `override=true` bypass is allowed. PHI routes require appropriate legal/contractual basis, minimum-necessary use and an approved provider/BAA arrangement where applicable.

## Billing models
- **Direct Stripe:** Valisentry is merchant of record. Tenant's Stripe customer and meter/subscription map to an internal billing account. Emit durable, idempotent usage events after the billable action; deliver to Stripe asynchronously with retry, deduplication, reconciliation and webhook signature verification.
- **Partner-managed (Tokatlas):** Tokatlas is merchant of record and invoices its customers. Valisentry authenticates Tokatlas as a trusted reseller, meters usage per downstream tenant, and provides signed usage statements or a partner settlement feed. Never assume Tokatlas has accepted the integration or that revenue share has been negotiated.
- **Internal/sponsored:** Contractually permitted non-billed accounts still generate usage for quotas and cost attribution.

Use separate contracts for `validation_request` and `override_review`. Pricing, minimum commitments, usage dimensions, currency, refund handling, tax, Stripe meter identifiers and partner revenue-share are commercial configuration, not caller-supplied request fields. Never accept arbitrary `payer_id` or `mode` from an untrusted API request.

## Implemented foundations
- `valinator.override_policy`: per-tenant deterministic eligibility checks for selected findings; requires complete assessment and reviewer approval where configured.
- `valinator.billing`: typed tenant/payer/mode usage event contract with deterministic event IDs for idempotent downstream processing.
- `tests/test_override_billing.py`: tenant isolation, reviewer requirement, non-overrideable findings, billing modes, event identity and bounds.

**Not yet implemented:** persistent tenants and principals, secure review workflow, override endpoints, single-use authorization tokens, ingestion-boundary enforcement, durable usage ledger, Stripe API calls, Stripe webhook verification, customer billing portal, Tokatlas partner contract and settlement integration. No charges have been created and no external payment credentials are required for these foundations.
