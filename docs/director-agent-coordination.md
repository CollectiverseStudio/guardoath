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
