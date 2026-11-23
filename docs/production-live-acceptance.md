# Production live acceptance

This is the numbered, fifteen-area acceptance manifest for a GuardOath release.
It gives stable identifiers to requirements previously spread across the
deployment guide, security contracts and coordination log. It does not establish
that any earlier release completed these checks. Exact-source CI, a deployed
commit, public HTTP 200 responses and configuration inventories are separate
evidence; none is a substitute for an authenticated live customer journey.

Record the full source SHA, UTC verification time, deployment/configuration
revision, enabled capabilities and result for every row. Use **passed**,
**failed**, **blocked** or **not run**. Missing credentials, unavailable fixtures
or an unfinished sign-in are blocked, never passed. A fixture, mock provider or
local test must be labeled as such and cannot establish live provider success.
Do not publish credentials, customer identities, prompts, model answers, raw
provider errors or private signed reference values in the evidence report.

## Fifteen required acceptance areas

| ID | Area | Required live evidence |
| --- | --- | --- |
| LIVE-01 | Accepted source and deployment identity | Current main's complete required CI jobs succeeded at the exact candidate SHA; `/healthz` reports that SHA; the active Lambda archive digest matches the built artifact and its update completed successfully. Record any unresolved issue #30 risk. |
| LIVE-02 | Dashboard, assets and customer-visible controls | The full dashboard HTML and every versioned asset match that checkout. In a real browser, the current Input/Output, Policy, API access and Models controls load without a JavaScript error; desktop and narrow layouts remain usable. |
| LIVE-03 | Authentication and tenant isolation | An existing valid customer credential reaches its own application. Anonymous, invalid, revoked and wrong-tenant credentials cannot read or mutate another tenant, policy or receipt. Use independently owned test accounts; one successful account does not prove isolation. |
| LIVE-04 | Customer registration and sign-in | Complete the configured native registration/confirmation/sign-in journey with an authorized test identity; verify expiry/sign-out and the intended MFA behavior. Registration alone does not grant paid entitlement or an application API key. Existing-user login alone does not establish new-account registration. |
| LIVE-05 | Subscription and credential lifecycle | In the deliberately selected Stripe environment, verify unpaid denial, server-created customer binding, completed checkout, trusted provider reconciliation, the saved plan allowance and permitted API-key issuance/revocation. A checkout return URL cannot activate access. Also verify cancellation/non-active reconciliation using an authorized test subscription. Test-mode success is not live-money billing acceptance. |
| LIVE-06 | Saved tenant policy and revision authority | Save deliberate Input/Output family and per-rule actions through the customer UI, reload and verify the authoritative record/revision. Check an explicitly disabled rule remains disabled, unrelated rules remain active and stale revision conflicts do not overwrite later changes. Restore the approved intended policy after controlled test changes. |
| LIVE-07 | Benign Input validation | Send synthetic benign text through the authenticated Input API and dashboard. Verify complete, non-simulated checking with the actual saved policy, expected findings/decision and consistent usage accounting; HTTP 200 alone is insufficient. |
| LIVE-08 | Input findings and enforcement | Exercise known matching fixtures for enabled rules and confirm all relevant findings, readable rule names and saved Flag/Block behavior. For guarded invocation, a blocked or incomplete Input result must not dispatch a provider or reserve another generation. |
| LIVE-09 | Output enforcement and user guidance | Exercise benign and known disallowed proposed answers through the Output API; verify complete checking and withheld output when required. In the Models UI, static GuardOath guidance remains distinct from released model text. No notice may reveal withheld content or claim a human was notified. |
| LIVE-10 | Attachment provenance and failed inspection | Use actual synthetic upload bytes for supported clean and flagged fixtures. Verify the intended automatic inspection behavior, saved explicit Off provenance and stage authority. Malformed, unreadable, oversized, unsupported and incomplete inspection must Block even when the tenant otherwise observes or flags. Do not alter unrelated legacy tenant settings. |
| LIVE-11 | Privacy, redaction and deadline behavior | Exercise the shipped synthetic PII/secrets and redaction/recheck fixtures on the live service; inspect complete versus incomplete status, transformed-only release and the original 500-ms deadline. A timeout stays fail-closed. Record observed latency and any tail failures; a finite passing sample does not close issue #30. |
| LIVE-12 | Trusted reference integrity | For an enabled reference-dependent feature, verify an authorized host-signed fixture and refusal of missing, expired, changed or unsigned authority. A customer-supplied claim must not become trusted. When the feature is intentionally unconfigured, verify its explicit unavailable/denial behavior and state that valid-reference execution was not tested. |
| LIVE-13 | Model availability, execution and receipts | Verify catalog readiness against the declared deployment profile. If invocation is enabled, complete an explicitly bounded real call, checked answer, durable signed receipt, request-ID recovery and replay without regeneration; exercise quotas and unknown/withheld outcomes using authorized synthetic cases. If intentionally disabled, verify unavailability and explicitly record that live inference was not accepted. |
| LIVE-14 | Owner administration and audit | Verify anonymous/customer/demo denial. If admin is enabled, complete a fresh native owner MFA login and successful protected data reads with persistent audit receipts. An enrollment flag, visible login page, AWS SSO session or configuration inventory is not current-login MFA proof. Exercise only approved reversible owner actions and reconcile their audit receipts. |
| LIVE-15 | Recovery, capacity and operational boundaries | Preserve a readable, identified previous Lambda artifact and the configuration needed for rollback; verify the restoration procedure is applicable without destroying tenant/receipt data. Record measured request/storage latency, bounded quotas, unknown-attempt recovery and sanitized errors. Preserve the stable receipt-signing key; do not clear reservations or regenerate to hide an uncertain outcome. Distinguish a prepared rollback from an actually exercised rollback. |

An intentionally disabled optional capability can pass its **disabled-behavior**
check only when that expected deployment profile is recorded before the run.
This never proves that capability works when enabled. For a model activation,
LIVE-13 requires a real approved provider call; for the enabled admin console,
LIVE-14 requires a real current owner MFA login. Record those narrower capability
outcomes alongside the overall release result.

## Reproducible starting commands

Run from the clean checkout of the exact candidate SHA. Reports under
`.guardoath-reports` are local operational evidence, not committed customer data.

```sh
python -m tools.verify_live_dashboard \
  --url https://guardoath.com --expected-sha FULL_CANDIDATE_SHA

python -m tools.verify_admin_activation \
  --profile collectiverse-prod --region us-east-1

python -m tools.verify_model_invocation \
  --application-id YOUR_APPLICATION_ID --expected-sha FULL_CANDIDATE_SHA
```

The first command establishes source parity, part of LIVE-01/02. The admin
command is a read-only configuration inventory and deliberately does not certify
MFA access. The model command uses an already issued customer key from
`GUARDOATH_API_KEY` and makes no generation POST by default; it does not create a
key, subscription or connection. Do not put keys in command arguments or commit
them. A valid customer dashboard session can exercise the browser model flow;
an owner admin session is not a customer session.

Use the [one-call procedure](model-invocation-live-acceptance.md) only after the
exact connection, limits and provider costs are approved and configured. Save
the request ID before sending; after an uncertain result, inspect that receipt
without generating again. Actual request bodies and decisions for the remaining
rows follow the [API contract](api-contract.md),
[customer guide](user-guide.md),
[guarded model contract](guarded-model-invocations.md) and
[owner administration guide](admin-console-operator.md). Use the named synthetic
fixtures in the corresponding acceptance tests, never live employee content.

## Evidence and handoff

Keep a table containing all fifteen stable IDs, result, bounded evidence
reference, observed limitation and next action. Include the source SHA and
configuration revision so later code or configuration changes cannot inherit an
old result silently. Mark evidence from different source/configuration revisions
as historical. Record test Stripe versus live Stripe explicitly.

The guarded deployment script preserves rollback and verifies live source
parity. Its success does not fill the remaining rows. Do not report **15/15**,
full production acceptance, verified owner MFA or successful paid inference
unless the corresponding live evidence exists. Complete every available check
and report blocked rows precisely; do not weaken access controls, customer
entitlement or fail-closed behavior to obtain a green result.
