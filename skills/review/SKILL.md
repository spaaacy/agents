---
name: review
description: Pre-PR self-review of a branch or PR. Product regressions and unrequested behavior changes first, then verified engineering findings ranked by blast radius. Run again on a PR it already reviewed, it reviews only what changed since (a follow-up review); `full` reviews everything again. Once the report is out, posts it with /post-review, including clean reviews, unless it would duplicate an existing review or the user asked not to post.
---

Review $ARGUMENTS. A PR number or PR URL means `gh pr view` + `gh pr diff` (both accept either form); a branch name is diffed against the repo's default branch; nothing means the PR or branch this conversation is about (`Shared skill arguments` in `~/.agents/AGENTS.md`), else the current branch against its base, including uncommitted changes. A target this skill reviewed before gets a follow-up review of what changed since (step 0); `full` in the arguments reviews the whole change again. Read-only on the code: never edit. Writes are limited to the review record and posting the completed review (below).

This is the author's own review before the PR goes up. The reader wrote the code with AI assistance and is worried about product changes slipping in unnoticed, so be blunt, lead with behavior, and never soften a finding because the author is the reader.

Flow: `/pickup` produced the branch; this is the first look at it, and each later run looks only at the commits that landed after the last one. Its output feeds `/post-review` (puts it on the PR as one review), which this skill runs once the report is out, even when it found nothing, unless the user asked not to post or the review would be a duplicate. Findings the author fixes on the branch go through the ordinary edit-test loop; a finding about agent behaviour with no scenario is fixed under the "Measure" section of `~/.agents/skills/fix/evals.md`. Once reviewers comment, the author's side is `/address-review`, and `/verify-fixes` checks that round. This skill never fixes anything.

## 0. First review or follow-up
Unless the arguments say `full`, find the last review this skill did of the target. Two places hold it:
- The record this skill keeps: `~/.agents/state/review/<owner>-<repo>-<pr number>.jsonl`, one JSON line per review, newest last. A branch with no PR uses `<owner>-<repo>-<branch, / replaced by ->.jsonl`; for a PR, read its branch's file too, since a review from before the PR opened counts.
- The PR's own reviews: `gh pr view <pr> --json reviews`. A review whose body holds `<!-- review-skill head=<sha> -->` was posted by `/post-review` and reviewed that sha; one holding `<!-- slack-pr-review:` was posted by the Slack review bot and reviewed its `commit.oid`.
The newest of these, by time, is the last review and its head the last reviewed head. Nothing found: a first review, straight to step 1.

Otherwise work out what is new since that head:
- Make sure the commit is here: `git rev-parse --verify <sha>^{commit}`, else `git fetch origin <sha>`. If it cannot be had (rewritten away and no longer served), do a full review and say why in the first line.
- Same head as now, and for a branch review no uncommitted changes: say "Nothing new since the review of <short sha> on <date>. `/review <target> full` reviews it all again." Do not re-review or append another record. If the completed report exists only locally and the PR is open, load it and follow Then post below; otherwise link the existing review and stop. An explicit request not to post still takes precedence.
- The last head is an ancestor of the current one (`git merge-base --is-ancestor`) and no merge commit sits between them (`git log --merges <old>..HEAD` is empty): the new work is `git log <old>..HEAD` and `git diff <old>..HEAD`.
- Otherwise the branch was rebased, force-pushed or had its base merged in: `git range-diff <merge-base of base and old>..<old> <merge-base of base and HEAD>..HEAD`. Commits marked `>` (new) or `!` (changed; the patch difference shows how) are the new work, and `=` commits were reviewed already. Commits the base brought in are not the PR's and are not reviewed, but a merge commit's conflict resolutions are (`git show --remerge-diff <merge>`). If the two sides cannot be paired (a squash), do a full review and say why.
- In a branch review, uncommitted changes are always new. Uncommitted work an earlier review saw is reviewed again once it is committed.

Read the last review's findings too: the record's `report`, or the posted review's body plus its inline comments (`gh api repos/{owner}/{repo}/pulls/{n}/reviews/{id}/comments`; when that read is refused, the body alone).

A follow-up runs steps 1 to 4 over the new work only:
- Context is the same: the ticket and the whole PR, because new work is judged in its context.
- The behavior ledger, intent and findings cover only what the new work changes or breaks. A finding can sit in code the new work did not touch, when the new work is what breaks it.
- Earlier findings are not reported again. When the new work touches one, say in one line whether it now looks fixed or still stands; checking every earlier finding is `/verify-fixes`'s job.
- Anything the last report listed under Not verified that the new work touches is checked now, or listed again.

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
Open with one line: `<n> blocking · <n> should fix · <n> polish`, or "No blocking issues". A follow-up opens with `Follow-up on <old short sha>..<new short sha>: ` before it, then adds **New since last review** (one line per new or changed commit: short sha and subject) and, when the new work touched any, **Earlier findings touched** (one line each: fixed or still stands). Everything below then covers the new work only.

**Ask** — the ticket in one paragraph.

**Behavior ledger** — every observable change, each tagged INTENDED or UNREQUESTED, with `path:line`. Unrequested ones first.

**Intent** — delivered / partial / not delivered, gaps first, then scope creep.

**Findings** — Blocking / Should fix / Polish, product findings before engineering findings within each tier. Each:
- `path:line` — the issue, its trigger and consequence in one or two sentences. Fix: the smallest concrete change, or "delete". Verified by: what you ran or read.

**Not verified** — tests not run, lint not run, assumptions inferred rather than checked.

**Merge** — Ready to open / Fix first / Rethink, one sentence why.

**Reviewed at** — the full head sha the review read, plus "with uncommitted changes" when it read them. `/post-review` marks the posted review with it.

## Record it
Once the report is out, append one JSON line to the record file step 0 names (`mkdir -p ~/.agents/state/review` first): `{"reviewed_at": "<ISO time>", "repo": "<owner>/<repo>", "pr": <number or null>, "branch": "<head branch>", "head": "<full sha reviewed>", "base": "<base branch>", "base_sha": "<sha>", "kind": "full" or "follow-up", "since": "<last reviewed head or null>", "uncommitted": <true or false>, "report": "<the report as shown>"}`. Write it with a short script, never by hand-escaping the report on a command line. If the write is refused (an unattended run), go on without it: a posted review carries its head on GitHub.

## Then post
Once the report is out, find the open PR for the target (the PR reviewed, or the open PR for the reviewed branch: `gh pr view <branch> --json number,state`). Run `/post-review <target>` without another question, including when the review found nothing. Aakif's standing preference is to post reviews unless that creates a duplicate; an explicit request not to post overrides this default.

Before posting, follow `/post-review`'s duplicate check against the PR's existing reviews and comments. If the same reviewed head and result are already covered, link that review and skip another post. A local review record alone is not a posted review. For several PRs, check and post each separately. With no open PR there is nothing to post to: end with the review outcome and any first fix needed on the branch.
