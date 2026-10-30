# GuardOath — Director ↔ Validation Agent Coordination

**Status:** Active shared coordination log  
**Established:** 2026-10-30  
**Repository:** `CollectiverseStudio/guardoath` · `main`

## Rule #1 — Mandatory first action before ANY GuardOath work

**STOP AND READ THIS FILE FIRST.** Before investigating, planning, coding, editing, testing, running CI, opening a PR, committing, or modifying documentation for GuardOath, both the Director and Atlas must fetch and read the **latest** `docs/director-agent-coordination.md` from GitHub `main`. Review open requests, ownership, active work, and latest responses. Then check the current `main` SHA and relevant CI results. Do not rely on a previously cached copy or conversation summary.

If GitHub or this file is unavailable, **do not make repository changes**; report the blocker and request coordination. For any overlapping/reserved files, request and receive acknowledgement here before editing. After each meaningful work batch, append the changed files, commit, tests, CI status and follow-up. This is the highest-priority project workflow rule and applies even to apparently isolated tasks.

## Purpose

This file is the shared, polite, durable communication channel between:

- **Director:** leads the GuardOath website, branding, layout, website assets, and site publishing. Coordinates overall product integration.
- **Validation agent (Atlas):** develops GuardOath validation/testing capabilities, use-case audit evidence, evaluation tooling, and technical documentation.

Both agents are working concurrently. **Neither agent should overwrite or undo the other's work.** Greg is the decision-maker when priorities or ownership conflict. Both agents must read the latest version of this file before **any** GuardOath work, including non-overlapping tasks.

## Ownership and edit boundaries

| Area | Primary owner | Other agent's responsibility |
|---|---|---|
| Website pages, homepage, public site, visual identity, video player, web assets | Director | Atlas does not modify without explicit agreement |
| Dashboard HTML/CSS/JS and user-facing site experience | Director | Atlas raises requested changes here instead of editing |
| GuardOath core validation checkers, new tests and use-case evidence | Atlas, unless Director has a conflicting active task | Director communicates planned overlapping edits before starting |
| Shared `guardoath/app.py`, `guardoath/models.py`, `guardoath/engine.py`, privacy, tenants, billing, CI | Coordinate explicitly; Director retains prior reserved-file ownership | Atlas records a request and waits for acknowledgement before touching |
| `docs/use-case-*.md`, `docs/additional-use-cases.md`, `docs/use-case-manifest.json`, `docs/catalog-audit-notes.md`, new Atlas test files | Atlas | Director avoids concurrent edits unless agreed |
| `docs/director-agent-coordination.md` (this file) | Shared | Append dated entries; avoid rewriting others' entries |

Ownership is for avoiding collisions, not limiting Greg's authority. Any explicit new instruction from Greg supersedes this table.

## Coordination protocol

1. **Before any work (mandatory Rule #1):** fetch and read the latest version of this file from GitHub `main`, then inspect current `main` and GitHub Actions. If touching a shared/reserved file, add a concise request under **Open requests**, specifying paths, purpose, risk and desired handoff. Do not begin conflicting edits until acknowledged.
2. **During work:** prefer small coherent commits, avoid force-push/rebase of others' published work, and never weaken/delete tests or eval cases merely to make CI green.
3. **After work:** append an entry to **Change log** with date, agent, changed paths, commit SHA, test run/results, documentation changes and follow-up.
4. **If the other agent's changes land first:** fetch latest `main`, reconcile before writing, and report any conflict. Do not silently overwrite.
5. **When blocked:** leave a concrete request here, keep working only on non-overlapping files, and ask Greg to arbitrate only when necessary.
6. **Status language:** distinguish implemented, committed, tested, CI-green, proposed, and deployed. A passing test is not proof that all 200 use cases are implemented.

## Open requests

| ID | From → To | Request | Affected paths | Status |
|---|---|---|---|---|
| COORD-001 | Atlas → Director | Please confirm this shared coordination protocol and continue owning all website/dashboard work. Atlas will not modify those files without approval. | Website files, `guardoath/static/*` | Awaiting Director acknowledgement |
| COORD-002 | Atlas → Director | Please review the latest 94 new parametrized API regression scenarios, and coordinate if you are changing shared schema, API, engine or policy logic. | `tests/test_catalog_partial_boundaries.py`, `tests/test_catalog_partial_contracts.py`, shared engine/API | FYI / review requested |
| COORD-003 | Atlas → Director | The website launch video is documented but its final MP4 has not been uploaded to GitHub or deployed. Please handle the video player, accessibility/captions and hosting as part of your website ownership. | `docs/launch-video-website.md`, website media | Director-owned; no action by Atlas |

## Change log

### 2026-10-30 — Atlas — initial coordination handoff

- Created this coordination file after Greg explicitly requested a shared channel and courteous parallel development.
- Recent validation work: committed two new API regression test files with **94 parametrized scenarios** (36 boundary, 58 contract). The baseline Tests #223 was green before the new tests. New CI verification is still required; do not claim these scenarios pass until the latest run is green.
- Documentation: `docs/catalog-audit-notes.md` records the test integration and pending verification.
- Director's website, dashboard and branding files were **not modified** by this coordination work.
- Reference handoffs: [Director coding-agent handoff](director-coding-agent-handoff.md), [catalog audit notes](catalog-audit-notes.md), [input-validation progress](input-validation-implementation-progress.md), [website launch video](launch-video-website.md).

## Director acknowledgement / response

Director: Please append your acknowledgement, active file ownership, any overlapping work, and requests for Atlas here. No need to rewrite or delete existing entries. If a requested change affects shared code, propose a sequence and name the files.

## Next check

Both agents should consult this file before the next overlapping commit and update it after material changes. This is a **GitHub document**, not an automatic messaging system; changes become visible when each agent reads the current repository.


### 2026-10-30 — Atlas — direct GitHub CI verification and Director coordination

Greg confirmed both agents can read GitHub Actions directly; no manual forwarding of logs is necessary. Atlas independently checked workflow runs: **Tests #227** for commit `2ef672c0` completed **successfully**, following integration of the 94 API regression scenarios. **Tests #228** for the coordination commit `64b5e5e9` was **in progress** when checked. These are workflow-level conclusions; consult individual jobs and logs if diagnosing a failure.

**Working agreement:** Atlas checks CI and logs directly, adds specific cross-owner requests here with workflow run, failing test, affected paths, and suggested action. Director may reply in this file and independently inspect GitHub. Neither agent should wait for Greg to relay routine CI information. This Markdown file is asynchronous coordination, not a direct-message or notification channel; agents must fetch the latest revision to see new entries.

### 2026-10-30 — Atlas — Rule #1 established at Greg's request

Greg explicitly directed that **checking this communication file with the other agent is the first step before any GuardOath work**. Rule #1 above now applies to both agents, including testing, documentation, planning and code changes. Atlas will follow it in future GuardOath tasks. This GitHub document is the durable shared record; ChatGPT account Memory is separately controlled by the user and cannot be guaranteed to update synchronously from this commit.
