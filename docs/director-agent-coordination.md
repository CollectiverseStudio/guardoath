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


## 2026-11-09 Atlas — ordinary Field Comparisons saved Output-Off integration and real 500-rule customer regression
- Current governing override reviewed first: Greg owns final decisions, Atlas exclusively owns all FC #28/#31 with no competing Codex claims; Director removed. Then `AGENTS.md`, recent commits, open issues/comments, and exact known-run individual CI jobs inspected. No separate deployment approval gate; exact source SHA CI, security/cost constraints and 15 live post-deploy checks still required.
- **Implemented** `guardoath/static/comparison_editor.js`: [eafada96](https://github.com/CollectiverseStudio/guardoath/commit/eafada96ccc9a4c3db235bd19f08763a0f298a00) shows authoritative saved ordinary per-stage Off in the existing tenant editor; pending changes instead show an unsaved warning and cannot masquerade as the saved definition. No action/Engine change.
- **Integrated actual non-mocked authenticated browser regression** `tests/browser/field_comparison_mixed_family_scale.spec.js`: [4276d66a](https://github.com/CollectiverseStudio/guardoath/commit/4276d66a4fca4705390ef6a5d6151d8953c1177c) and [c8f027ff](https://github.com/CollectiverseStudio/guardoath/commit/c8f027ff41cef3a4ff7ef7c714febbe189b85b04): 500 ordinary/arithmetic/identity/conversion rules, two separate Output-Off overrides (advanced conversion #499 and ordinary #000), input still exact two findings/Block, output omits those two only, saved revisions and all definitions survive; pending ordinary pointer edit warns and reconnect does not mutate tenant. Guide [62e18807](https://github.com/CollectiverseStudio/guardoath/commit/62e188079e84f6308e90b4d891e657c2133e19cb). Issue #31 [comment 6757403659](https://github.com/CollectiverseStudio/guardoath/issues/31#issuecomment-6757403659).
- **Verification actually performed:** re-read current GitHub blob SHAs, V8 syntax-compiled both JavaScript files, and ran the exact `comparisonEditor.render` and Edit/Submit logic under a minimal V8 synthetic DOM; five assertions passed (saved Output Off visible, Input not mislabeled, changed-draft warning, saved label suppressed until Save, callback invoked). This *does not* replace actual Playwright. Last individually checked earlier source [Tests 42726829622](https://github.com/CollectiverseStudio/guardoath/actions/runs/42726829622) 13/13 with dashboard job 129534493501 success, [FC 42726829173](https://github.com/CollectiverseStudio/guardoath/actions/runs/42726829173) strict job 129534490702 success, [CodeQL 42726829520](https://github.com/CollectiverseStudio/guardoath/actions/runs/42726829520) 3/3. Later but still predecessor [Tests 42726884254](https://github.com/CollectiverseStudio/guardoath/actions/runs/42726884254) 13/13 and [CodeQL 42726883260](https://github.com/CollectiverseStudio/guardoath/actions/runs/42726883260) 3/3. **The latest new FC SHA c8f027ff is NOT independently job-verified** (connector commit run query PR-only; no local GitHub DNS); no release or deployment assertion.
- **Next gates:** exact-SHA complete Tests/CodeQL/strict FC and Playwright, then live 15-check release validation, #30 intermittent original 500ms PII/Secrets/recheck failure reproduction and correction, #32 authoritative saved-Off attachment provenance, host-signed trusted-reference authority. No timeout increase, provider expense, forced push or live tenant mutation.


## 2026-11-09 Atlas — ordinary FC editor differentiates saved parent-check Off from rule-level Off
- Read current top-of-main override first (Atlas exclusive FC #28/#31; removed prior agent), then AGENTS.md, fresh main commits, open issues and recent #28/#31/#30/#32/#33 dated comments. Individual preceding CI jobs fetched: [Tests 42726829622](https://github.com/CollectiverseStudio/guardoath/actions/runs/42726829622) 13/13 success incl. dashboard job 129534493501, [strict FC 42726829173](https://github.com/CollectiverseStudio/guardoath/actions/runs/42726829173) job 129534490702 success, [CodeQL 42726829520](https://github.com/CollectiverseStudio/guardoath/actions/runs/42726829520) 3/3. Also later predecessor [Tests 42726884254](https://github.com/CollectiverseStudio/guardoath/actions/runs/42726884254) 13/13 and [CodeQL 42726883260](https://github.com/CollectiverseStudio/guardoath/actions/runs/42726883260) 3/3. Neither verifies new code.
- **Actual source change** [4e9d8c34](https://github.com/CollectiverseStudio/guardoath/commit/4e9d8c34b7c60fdbf543ded2848411214b77f6e4) in `guardoath/static/comparison_editor.js`: saved tenant Input/Output `cross_field` check-wide Off now has precedence in ordinary rule editor over individual saved Off; correctly explains a rule cannot execute if its parent check is Off even if its own rule action is Block. No policy/Engine/action changes. Unsaved draft changes are not misreported as saved.
- **Authenticated no-mock acceptance** [09008ea9](https://github.com/CollectiverseStudio/guardoath/commit/09008ea93621395620d43dcab26edef603d6f3d1), [706775fb](https://github.com/CollectiverseStudio/guardoath/commit/706775fb00aacc2104013c04419806b11562858b) and [e47514ed](https://github.com/CollectiverseStudio/guardoath/commit/e47514ed0c2009071ce29f25d4e0c37c4ffcd8a1) in `tests/browser/field_comparison_mixed_family_scale.spec.js`: 51-rule mixed portfolio stage-action UI, saved revision and reconnect; check-wide Input Off explicitly different from prior own-rule Output Off, both real shared Engine stages prove Input no business findings/Allow and Output independent Block with only enabled ordinary finding, revision unchanged. 500-rule portfolio test retained. Guide [93a8bf4d](https://github.com/CollectiverseStudio/guardoath/commit/93a8bf4dcf7690a3430a591d713adbd9d4cb0e73).
- **Executed current-source checks:** JS source and browser-spec V8 syntax success. V8 source-executed synthetic DOM `comparisonEditor.render` passed 3 positive/negative checks for saved parent Off precedence, saved own Off, and enabled rule no false Off. Path-triggered strict FC workflow includes changed editor/spec. Issue #31 [comment 6757955989](https://github.com/CollectiverseStudio/guardoath/issues/31#issuecomment-6757955989).
- **Still not accepted:** no actual new-SHA Playwright/13 Tests/3 CodeQL/FC strict pass obtained. Commit workflow lookup returns zero due PR-only connector filter, combined status empty; no GitHub Actions dispatch available. No AWS/production/tenant changes. #30 intermittent 500-ms PII/Secrets/redaction recheck, #32 persisted Off audit and signed references, live 15 checks remain open. No budget increase, skip, provider purchase or force.
