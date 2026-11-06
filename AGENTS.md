# Repository workflow

## Rule #1: read the coordination file first

Before any GuardOath work (investigating, planning, coding, editing, testing, running CI, committing or editing docs), fetch and read the latest `docs/director-agent-coordination.md` from GitHub `main`, then check the current `main` SHA and CI. Do not rely on a cached copy or a summary. If the file or GitHub is unavailable, make no repository changes and report the blocker. For shared or reserved files, request and receive acknowledgement in that file before editing. After each batch of work, append the changed files, commit, tests, CI status and follow-up there.

## Current ownership (Greg decision, 2026-11-05)

- **The Director has been removed from active GuardOath work.** Do not request Director acknowledgements, assign Director tasks or treat old Director reservations as active. The historical coordination file is preserved for audit; its topmost governance override supersedes old assignments.
- Atlas/GuardOath coordination can maintain website/dashboard, API, engine, tenant, privacy, security, CI and deployment paths previously reserved to the Director, after fresh main/ownership checks and respecting any new concurrent Codex or human claims. Greg remains the final decision-maker.
- Codex retains Field Comparisons Issue #28; do not overwrite concurrent Codex changes. Continue recording work/claims in the coordination file.
- The separate production approval gate was removed, but CI, fail-closed security requirements, AWS cost restrictions, rollback procedures and 15 post-deployment verification checks remain.

## Other rules

- Work directly on `main`. Do not create feature branches unless Greg explicitly requests a workflow change.
- Preserve existing work, synchronize with `main`, and use non-forced updates.
- Review changes and run relevant checks before committing. Verify CI after publishing to `main`.
- Complete a coherent validation-group batch (implementation, positive/benign/failure tests, dashboard examples, guide and CI) before handoff; avoid tiny feature-by-feature handoffs.
- Maintain `docs/user-guide.md` alongside every check: purpose, example/use case, expected behavior, configuration and limitations.
- Use only the GuardOath name. Do not add references to former product names, compatibility code or environment-variable fallbacks for them, or migration notes.
