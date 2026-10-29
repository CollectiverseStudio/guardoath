# Input finding overrides and multi-finding responses — design specification

## Decision
Return all independently evaluable findings in one assessment, bounded by policy deadlines and resource budgets. Do not stop at the first ordinary finding. If parsing, authentication, ingress limits, privacy routing, or dependencies prevent further checks, return completed findings **and** an explicit incomplete/unevaluated list. Never label a partially evaluated request clean.

## Overrides
A user may request review or an exception, but may not self-authorize bypassing policy. Separate:
- `correctable`: resubmit corrected content; no override.
- `acknowledgeable`: customer policy permits low-risk, possibly false-positive finding to be acknowledged by an authenticated principal.
- `approval_required`: privileged approval and a documented approved data route are required (for example, genuine PHI).
- `non_overridable`: authentication, tenant authorization, execution/sandbox limits, tool permissions, regulatory/provider routing boundaries and hard resource limits.

An ordinary end-user acknowledgment is not a legal basis to disclose another person's PHI. A PHI-approved workflow must have appropriate authorization, permitted provider/BAA arrangements when applicable, purpose restrictions, minimum-necessary processing and customer controls. A false positive can be reviewed and reclassified with evidence; it does not automatically authorize disclosure.

## Proposed API workflow (not yet implemented)
1. `POST /v1/checks/input` returns `check_id`, overall status, complete findings, per-check statuses, policy/pack versions and per-finding `override_eligibility` without echoing raw sensitive content.
2. `POST /v1/overrides/request` references `check_id`, selected finding IDs, reason and authenticated actor. Do not accept caller-supplied severity, rule definitions or an unrestricted bypass flag.
3. Authorized reviewer or trusted configured rule approves/denies the specific finding IDs; issue a cryptographically protected, short-lived, single-use exception bound to tenant, principal, application, content digest, policy version, pack versions, destination/provider, purpose and findings. Store only minimal redacted evidence.
4. On resubmission, **rerun mandatory CORE and all non-exempt checks**, validate the exception atomically against replay and changes, and require downstream destination authorization. Any new/changed finding or missing check fails safely.
5. The host must enforce the resulting decision at the actual AI ingestion boundary. Observe-mode recommended decisions are not equivalent to enforced blocks.

## Multiple findings
- Return all 3 findings if the corresponding checks completed, even when one is blocking.
- Deduplicate by stable rule ID and occurrence scope; include severity, explanation, checker version, action, remediation and eligibility.
- Do not reveal sensitive matched values, secrets, hidden instructions or provider error bodies.
- Enforce limits on finding counts and response sizes; report truncation/incomplete coverage explicitly.
- Distinguish `flagged` from `error`, `skipped`, `unsupported`, and `not_run`.
- Execute independent local checks without unnecessary short-circuiting. Keep external services gated by explicit privacy/egress permission; a local PHI finding must not trigger unapproved external evaluation.

## Acceptance criteria
- Three independent findings return all three, stable ordering, no raw PHI echo.
- One acknowledged finding does not suppress two other findings.
- Unauthorized user, other tenant, expired/replayed exception, modified content, changed policy/provider or changed pack is rejected.
- Non-overridable findings remain enforced.
- Required skipped/error checks produce incomplete assessment and cannot be overridden as clean.
- Provider/PHI routing disallowed: no egress even with acknowledgment.
- Auditable request/approval/replay-denial records without storing raw PHI by default.

Status: requirements only. No override endpoints, token issuance or approval database are implemented by this document.
