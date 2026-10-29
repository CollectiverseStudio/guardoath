# Repository workflow

- Work directly on `main`. Do not create feature branches unless Greg explicitly requests a workflow change.
- Preserve existing work, synchronize with `main`, and use non-forced updates.
- Review changes and run relevant checks before committing. Verify CI after publishing to `main`.
- Complete a coherent validation-group batch (implementation, positive/benign/failure tests, dashboard examples, guide and CI) before handoff; avoid tiny feature-by-feature handoffs.
- Maintain `docs/user-guide.md` alongside every check: purpose, example/use case, expected behavior, configuration and limitations.
- Use only the GuardOath name. Do not add references to former product names, compatibility code or environment-variable fallbacks for them, or migration notes.
