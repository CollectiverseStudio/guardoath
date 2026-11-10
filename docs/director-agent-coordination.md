# GuardOath engineering coordination — Atlas authority

Updated 2026-11-09. Greg explicitly authorized replacing the empty coordination file and resuming implementation.

## Governing decisions
- Atlas coordinates engineering and owns Field Comparisons issues #28 and #31, including tenant/API/dashboard/tests.
- The Director is removed from active work. Do not request Director acknowledgements or assign Director tasks.
- Work directly on main, preserve concurrent changes, avoid forced updates, and inspect current ownership and CI before edits.
- New validation features are not complete until tenant configuration, API, dashboard, documentation and browser/regression coverage are integrated.
- Registration supports native Cognito email/password; Google federation is optional. Registration does not grant a paid entitlement.
- Stripe subscription activation must come only from trusted provider reconciliation, never a browser checkout redirect.
- Attachment inspection failures must BLOCK even in observe mode; automatic attachment checks require actual bytes unless explicitly disabled.
- Production deployment is authorized, subject to CI, cost controls, rollback and the 15 verification checks.

## Atlas work log
- 2026-11-09: Recreated this file after GitHub returned an empty coordination file. Greg explicitly approved this restoration.
- Authentication implementation commits: dc91106, 5463187, eedc4e8, e0d04ef, ca6f191, c4b46ac, 4bf055c, b17f50f, e574daa, 163d005.
- Current release status: source commits made; end-to-end production registration and payment verification pending.
- Next focus: authenticated Stripe checkout, reconciliation, paid entitlement, browser customer journey and CI. No separate Director gate.

## 2026-11-09 Atlas billing UI batch
- Changed: `guardoath/static/dashboard.js`, `tests/browser/customer_registration_status.spec.js`.
- Commits: ab0f005, f939ac9, 923bd2e, fc1482b.
- Checkout success query string does not grant entitlement; unpaid account shows verification pending, cannot create an API key, and cannot open paid billing management.
- Added browser regression for checkout success without server-confirmed active subscription; updated unpaid customer portal expectations.
- CI and live Stripe/browser verification: not yet confirmed. Production not deployed in this batch.

## 2026-11-09 Atlas checkout API regression
- Commit f1449b5: tests/test_account_registration_entitlement.py adds mocked Stripe customer provisioning and checkout success/failure cases.
- Expected: Stripe customer binding can persist, but a returned checkout URL or provider failure never grants active subscription, paid checks or API keys. Repeat checkout reuses the existing customer.
- CI status unavailable from GitHub connector; live browser and AWS verification pending. No production deployment.

## 2026-11-09 Atlas Stripe configuration safety fix
- Commits 4674477 and 6d1a54f: app.py validates both server Stripe secret and price before creating a Stripe customer; test_account_registration_entitlement.py asserts missing price causes HTTP 503 with no provider call or tenant billing binding.
- CI: connector returned no statuses for preceding main SHA. No live Stripe/AWS deployment performed.

## 2026-11-09 Atlas malformed billing config hardening
- Commits 078b0c7 and 490e26c: validate Stripe secret shape and price identifier before any Stripe customer creation; regression covers publishable key, invalid/missing price ID, and malformed price.
- CI and live deployment not yet verified.

## 2026-11-09 Atlas customer API key visibility
- Commits 3521f30, 2e6cafc, d79bb62: dashboard HTML now distinguishes public demo key connection from customer sign-in; API key management panel is displayed only for a signed-in, non-demo tenant. Browser regression asserts public demo hides customer key controls.
- API keys remain usable for programmatic validation after issuance; owner session is required for management.
- CI and live deployment not yet verified.

## 2026-11-09 Atlas customer key session authority
- Commits be9e49e and 4927514: dashboard key listing/create/revoke explicitly use the current signed-in Cognito session and refuse demo/key-only contexts. Key listing fails closed when owner verification fails.
- Browser regression verifies X-GuardOath-Session and absence of X-GuardOath-Key during customer key management.
- CI and live production browser verification pending.

## 2026-11-09 Atlas key management resilience
- Commits 05162d5 and 69bd290: ignore stale key API responses when customer session changes, reject malformed key-list responses, disable key creation on failures, handle key mutation network errors without exposing old credentials.
- CI status unavailable; browser/production validation outstanding.

## 2026-11-09 Atlas CI-readiness regression batch
- Commits 2cea411, c5ecb33, 92d666e: stabilized public-demo browser test with explicit mocked sign-in configuration; added backend regression that programmatic API keys cannot list/create/revoke customer keys, with corrected key generator import.
- CI: push-triggered Tests workflow exists; GitHub connector commit status empty and workflow-run endpoint only reports pull-request runs, so push CI result remains unverified. Production release still held.

## 2026-11-09 Atlas paid owner key lifecycle regression
- Commit 9efedc3: backend regression covers paid signed-in owner listing, one-time key creation, two-key limit, revocation, and denial of account management using a newly issued application API key.
- Tests committed but not executed locally (GitHub checkout network unavailable); CI status not confirmed. Production remains held.

## 2026-11-09 Atlas CI manual dispatch
- Commit 8fd0573: .github/workflows/tests.yml now supports workflow_dispatch in addition to push and pull_request, permitting an explicit full Tests run on main without another code change.
- This connector does not expose workflow dispatch or push-run listing; no CI pass is claimed. Production remains gated on verified jobs and browser checks.

## 2026-11-09 Atlas one-time key secret lifecycle
- Commits 47c6b93 and 8045435: signedIn(false) clears the one-time plaintext API key, list and controls; browser regression checks sign-out hides panel and erases key field.
- CI not confirmed; production release remains gated.

## 2026-11-09 Atlas public dashboard key field correction
- Greg observed existing application API-key field outside sign-in. Confirmed in dashboard.html.
- Commits 9a5d696, 35db586, 76e33b1: hide customer-key-connect until signedIn(true), preserve public demo selector and demo API key, browser test checks field hidden publicly and visible for signed-in customer.
- Source committed, CI and production deployment not verified.

## 2026-11-09 Atlas existing customer key connection gate
- Commits 1c01609, 70e8501: existing application-key connection requires a current signed-in, non-demo customer before switching into API-key-only evaluation; browser test asserts customer key field visible after sign-in. Public demo path remains independent.
- CI and live deployment remain unverified.

## 2026-11-09 Atlas explicit signup route
- Commits 82becbd and e3fca7b: Register button now opens Cognito Hosted UI /signup instead of /oauth2/authorize, retaining OAuth PKCE, redirect URI and optional Google provider. Browser regression updated to assert /signup.
- Registration button remains conditional on server registration_enabled; production stack EnableSelfRegistration must be 1. No AWS access/CI confirmation in this session, so production not changed.
