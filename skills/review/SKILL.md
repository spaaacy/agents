---
name: review
description: Pre-PR self-review of a branch or PR. Product regressions and unrequested behavior changes first, then verified engineering findings ranked by blast radius.
---

Review $ARGUMENTS. A PR number or PR URL means `gh pr view` + `gh pr diff` (both accept either form); a branch name is diffed against the repo's default branch; nothing means the PR or branch this conversation is about (`Shared skill arguments` in `~/.agents/AGENTS.md`), else the current branch against its base, including uncommitted changes. Read-only: never edit.

This is the author's own review before the PR goes up. The reader wrote the code with AI assistance and is worried about product changes slipping in unnoticed, so be blunt, lead with behavior, and never soften a finding because the author is the reader.

Flow: `/pickup` produced the branch; this is the first look at it. Its output feeds `/post-review` (puts it on the PR as one review, after the user says post). Findings the author fixes on the branch go through the ordinary edit-test loop; a finding about agent behaviour with no scenario is fixed under the "Measure" section of `~/.agents/skills/fix/evals.md`. Once reviewers comment, the author's side is `/address-review`, and `/verify-fixes` checks that round. This skill never fixes anything.

## 1. Context
Find the ticket (Linear key in the branch name, commits, or PR body) and write down in one paragraph what was asked. Read the whole diff, then the code around it: callers and callees of every changed symbol, the tests covering them, sibling code that solves a similar problem (the baseline for "how this codebase does it"), and git log on the touched files. Read the rules: the repo's file under `repos/` next to this skill, named after the origin remote's repository (`basename -s .git "$(git remote get-url origin)"`, e.g. `repos/envoyai-backend.md`; this works from a linked worktree too), a `REVIEW.md` at the repo root if one exists, and every `CLAUDE.md` from the root down to the changed paths. No `repos/` file for this remote is fine; do not create one. Check the branch merges cleanly against its base.

## 2. Product review (report these first)
In this priority order:

1. **Unrequested behavior change.** Build a ledger of every observable behavior the diff changes: defaults, thresholds, conditions, ordering, removed branches, changed return values, changed error handling. For each, mark it INTENDED (traceable to the ticket) or UNREQUESTED. A "cleanup" that changes what a user, carrier, or client sees is unrequested. This ledger is the most important output of the review.
2. **Intent match.** Does the diff fully deliver the ticket? Name every gap first, then any scope beyond it. Every hunk should trace to the ask.
3. **Rollout risk.** Migration ordering and fresh-chain safety, deploy atomicity, what happens mid-deploy and on rollback, feature gating, cross-repo deploy order, backfills on existing rows. A backfill script with no runbook ticket linked in the PR body (`Working style` in `~/.agents/AGENTS.md`) is blocking: nothing else makes anyone run it.
4. **Human-facing copy.** Any change to spoken or emailed carrier text, UI strings, notifications, escalation cards, error messages. List each changed string and whether the ticket asked for it.
5. **Agent behavior drift.** Any change to prompts, tool docstrings, tool results, or entrypoint state assembly that shifts what the AI agent says or does. Each needs an eval scenario in the same PR, both polarities where relevant, and an adapter update if state assembly changed.
6. **Data semantics.** New columns or fields with ambiguous meaning, nullability that changes a read path, soft vs hard delete, audit rows, retention.
7. **Contract changes.** API response shapes, enum values, webhook payloads, realtime events, fields the frontend or extension reads. Name the consumer that would break.

## 3. Engineering review
In this priority order:

1. **Repo doctrine.** Quotable violations of the repo's `repos/<name>.md` rules / `CLAUDE.md`, and any rule sentence the change makes stale.
2. **Correctness.** Logic errors, edge cases, error and retry paths, transaction and concurrency boundaries, fail-open defaults, nullable misuse, unsafe casts, stringly-typed logic where a typed construct exists.
3. **Tests that would fail.** Changed behavior with no test that regresses if it breaks; pins that no longer test anything; mocks that make a test pass vacuously. Run the touched modules' tests if cheap.
4. **Mergeability.** Behind base, conflicts whose resolution would silently revert a sibling change, migration head forks.
5. **Observability.** A new failure mode with no log, metric, or monitor; whether tomorrow's incident on this code is diagnosable.
6. **Footprint.** Reimplemented helpers, duplication, speculative generality, dead code introduced or orphaned (confirm with a call-site search). Prefer deletion. This is polish and never blocks on its own.
7. **Docs currency.** CLAUDE.md, READMEs, generated API specs, and doc pointers made stale by the change.

## 4. Verify, then rank
Try to disprove every candidate against the actual code: read the callers, grep for usages, trace the path, run it when you can. Drop anything you cannot confirm. Do not report style, formatting, naming, anything the repo's linters or type checker already enforce, pre-existing issues the diff did not touch, or hypotheticals that need input the system never produces.

Severity is production blast radius, not how interesting the finding is:
- **Blocking**: an unrequested behavior change, a gap in the ticket's intent, a rollout hazard, a doctrine violation the repo calls critical, or wrong behavior / data loss / security / tenant scoping.
- **Should fix**: a real defect that is self-limiting or needs a rare precondition. Say what makes it self-limiting.
- **Polish**: footprint, consistency, docs. Say plainly that it is polish. Cap at five, count the rest.
If a severity rests on something inferred rather than checked, say what it drops to if the inference is wrong.

## Output
Open with one line: `<n> blocking · <n> should fix · <n> polish`, or "No blocking issues".

**Ask** — the ticket in one paragraph.

**Behavior ledger** — every observable change, each tagged INTENDED or UNREQUESTED, with `path:line`. Unrequested ones first.

**Intent** — delivered / partial / not delivered, gaps first, then scope creep.

**Findings** — Blocking / Should fix / Polish, product findings before engineering findings within each tier. Each:
- `path:line` — the issue, its trigger and consequence in one or two sentences. Fix: the smallest concrete change, or "delete". Verified by: what you ran or read.

**Not verified** — tests not run, lint not run, assumptions inferred rather than checked.

**Merge** — Ready to open / Fix first / Rethink, one sentence why.

**Next** — one line: `/post-review` to put this on the PR, or, when this was the author's own pre-PR pass, what to fix first on the branch.
