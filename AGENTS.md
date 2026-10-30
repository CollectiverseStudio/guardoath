# Repository workflow

## Rule #1: read the coordination file first

Before any GuardOath work (investigating, planning, coding, editing, testing, running CI, committing or editing docs), fetch and read the latest `docs/director-agent-coordination.md` from GitHub `main`, then check the current `main` SHA and CI. Do not rely on a cached copy or a summary. If the file or GitHub is unavailable, make no repository changes and report the blocker. For shared or reserved files, request and receive acknowledgement in that file before editing. After each batch of work, append the changed files, commit, tests, CI status and follow-up there.

## Other rules

- Work directly on `main`. Do not create feature branches unless Greg explicitly requests a workflow change.
- Preserve existing work, synchronize with `main`, and use non-forced updates.
- Review changes and run relevant checks before committing. Verify CI after publishing to `main`.
- Complete a coherent validation-group batch (implementation, positive/benign/failure tests, dashboard examples, guide and CI) before handoff; avoid tiny feature-by-feature handoffs.
- Maintain `docs/user-guide.md` alongside every check: purpose, example/use case, expected behavior, configuration and limitations.
- Use only the GuardOath name. Do not add references to former product names, compatibility code or environment-variable fallbacks for them, or migration notes.
