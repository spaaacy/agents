---
name: fix
description: Get a pull request, or every open PR for a Linear ticket, green and mergeable and keep it there - rebase onto the base when it falls behind, conflicts, or the user asks (regenerating generated files from the rebased tree), fix red CI, and triage and fix red agent evals (a PR's Eval sample, the nightly, or a scenario named by id). Rebases, CI fixes and eval fixes are pushed on their own; it stops to ask only for real calls (a product call, a standing eval red, a conflict it will not guess, a new Linear ticket). It never merges, approves, or enables auto-merge. Use when the user says fix, watch, rebase, bring up to date, catch up with development, "do the same" after a merge, CI is red, Eval sample failed, the nightly is red, or a scenario is flaky.
---

Target: `$ARGUMENTS`.

- A GitHub PR number or URL: that PR and the open PRs stacked on it. A bare number is looked up in the current repo first, then `envoyai-web`, `envoyai-chrome-plugin`, `envoyai-chat-ui` (backend PRs are in the 2000s, the others are lower; never guess across repos, ask if two match).
- A Linear key (`ENG-1234`) or Linear URL: every open PR for that ticket across the repos.
- `nightly`, one or more eval scenario ids (`in_esc_021`, or `in_esc_021.email-prompt` to pin the target), a GitHub Actions run id (`run 37505277132`), or pasted eval output (a step summary, a check title, a sticky `<!-- evalkit-summary -->` comment): evals only. Go straight to `evals.md` in this folder; there is no CI loop.
- Nothing: the PR this conversation is about (`Shared skill arguments` in `~/.agents/AGENTS.md`), else the open PR for the current branch (`gh pr view --json number`). No PR means ask which with the structured question tool and stop.
- A trailing `triage` stops evals after the triage report, changing nothing.
- A trailing `rebase`, or a request that says rebase, bring up to date, catch up with development, or "do the same" after a sibling merged: bring every PR level with its base this run, even when GitHub does not say it is behind.

**This skill never merges.** Not `gh pr merge`, not `gh pr merge --auto`, not the merge queue, not `gh pr review --approve`, not dismissing reviews, not editing branch protection, not pushing to the base branch, not squashing "just the last step". When a PR is green and mergeable the job is to say so and stop. If the user asks mid-run to merge, still refuse inside this skill: tell them it is one click for them, and that the skill will not do it. There is no phrasing of the request that changes this.

Invoking the skill is the go for rebasing and force-pushing (with lease) the PRs it names, pushing a fix commit for a red check, one re-run of an infrastructure failure per check, and the eval triage and fix `evals.md` lays out, including its Linear comment. It stops to ask only for the user's real calls: an eval product call, a standing eval red, a rebase conflict it will not guess, a test that pins the old behaviour, and creating a Linear ticket or editing one beyond a comment, which never happens without explicit approval. It never pushes to `development` or any branch but a PR's own head, never runs `az`, and never weakens a test, eval, golden, baseline, coverage floor, lint rule, or type check to get green.

One invocation is one tick. Under Claude Code, run it as `/loop /fix <target>` so the session re-checks on its own: each tick ends by scheduling the next wake-up (5 to 10 minutes while checks are running, 20 to 30 minutes while waiting on a human review, stop when every PR is done). Invoked without `/loop`, one tick is the whole run: report, and say that `/loop /fix <target>` keeps it watched. A tick that ends on a question (an eval product call or standing red, a conflict it will not guess, an author's spec call) pauses the loop until the user answers, so ask only when nothing else is left to do this tick.

Flow: `/pickup` opened the PRs; this runs beside the review loop, not inside it. It owns being level with the base, CI, and eval reds; review findings are `/address-review`'s (checked by `/verify-fixes`), and the merge is the user's. When a PR merges, `/linear-sweep` moves its ticket and `/cleanup` removes its worktree. The "Measure" section of `evals.md` is the shared rule for every skill that changes agent behaviour (`/pickup`, `/address-review` and `/verify-fixes` point at it).

## 1. Resolve the PRs and read their state

For a ticket, resolve it to its PRs and stack the way `/spotlight` step 1 does (exact-key search in every repo, follow bases and children, skip promotion and release PRs). Keep only OPEN PRs; a merged one is already in its base. Order them so a PR is handled after the PR it depends on: within a repo, parent before child; across repos, the backend before any frontend whose generated types come from it.

Per PR:

```
gh pr view <pr> --json number,title,state,isDraft,headRefName,baseRefName,headRefOid,mergeable,mergeStateStatus,reviewDecision,url
gh pr checks <pr>
```

Map it:
- `state` MERGED or CLOSED: drop it from the loop and report who merged or closed it.
- `mergeable` CONFLICTING, or `mergeStateStatus` DIRTY or BEHIND, or a rebase was asked for and the PR is not level with its base (`git merge-base --is-ancestor origin/<base> origin/<head>` fails): step 3. A conflicting PR gets no CI at all, and a check that is red only because the branch is stale goes green with the rebase, so rebase before fixing that PR's checks.
- Any check `fail`: step 4.
- Any check `pending`: nothing to fix yet; note which are still running and schedule the next tick for when the longest one should finish (Unit on the backend takes about 5 minutes, Eval sample longer). Skipped checks are fine; `Eval sample` skips on purpose when the PR is not eval-relevant.
- `UNKNOWN` for either field means GitHub is still computing; treat as pending.
- Everything passed and `mergeStateStatus` is CLEAN: done for that PR. BLOCKED with all checks green means a required review or an unmet rule; report what is missing (`reviewDecision`, required reviewers, a draft flag) and keep watching on the slow interval, since a review can come in.

A PR that is merely behind, on a repo that does not require up-to-date branches, is left alone unless a rebase was asked for: a rebase restarts CI and draws a new eval slice.

Say what changed since the last tick in one or two lines. A tick with nothing new is a no-op; do not restate the whole status.

## 2. Where to work

Never switch a main checkout's branch, never rebase or commit on one that is not on the PR's head branch, and never `git stash`. Per PR:
- A linked worktree already on the head branch (`git worktree list --porcelain`): use it if `git status --porcelain --untracked-files=no` is clean. Dirty means report and skip that PR this tick; never rebase or commit on top of someone's uncommitted work.
- The main checkout is on the head branch (`/spotlight` put it there): work there, with no new worktree (`Working style` in `~/.agents/AGENTS.md`). Rebase, commit and push from it, so the checkout the user tests on stays the PR. Tracked changes mean report and skip that PR this tick, as for a dirty worktree. If the permission system refuses a write there, report it and stop for that PR; do not fall back to a worktree.
- Otherwise `git fetch origin` and `git worktree add -B <head> ~/dev/.claude/worktrees/<branch-slug> origin/<head>`.

Reuse the worktree `/pickup` or `/address-review` made; never create a second one on the same branch. Setup per repo: a new backend worktree runs `<main checkout venv>/bin/python -m scripts.dev.worktree_bootstrap` once (a plain `poetry run` there finds no `poe` until the bootstrap links the venv; add `--with-db` for an agent-loop eval target), and never `poetry install` or `docker compose` from a worktree. Frontends get `node_modules` as an APFS clone of the main checkout's (`cp -Rc`), not a symlink, because vite refuses files reached through a symlink outside the worktree.

## 3. Behind, conflicting, or asked to rebase

Follow `rebase.md` in this folder: fetch, rebase onto the base (a stacked child onto its parent's freshly pushed head, in the same run), resolve, regenerate generated files from the rebased tree, run the checks, push with lease. Stacked PRs: if the base is not the repo's default branch, the base is someone's branch; rebase onto that branch's current tip only.

## 4. Red checks

Read the failure, not the summary: `gh run view <run-id> --log-failed` (run id from the check's link), or the check's own page for codecov and the eval sample. Then:
- **Infra failure** (runner lost, timeout with no test failure, network, a `skipping`/cancelled from a concurrency group): `gh run rerun <run-id> --failed`, once. A second infra failure on the same check is reported, not retried again.
- **Lint, format, type, drift check** (ruff, mypy, prettier, eslint, svelte-check, the str-enum lint, migration head, `openapi-v1.json` drift, prompt goldens drift): reproduce locally in the worktree on the failing files only (backend mypy as `mypy --no-site-packages --follow-imports=silent <files>`, per `Running checks` in `~/.agents/AGENTS.md`), fix or regenerate, commit, push.
- **Test failure**: reproduce locally, read the test and the code it covers, fix the code. If the test asserts the OLD behaviour and the PR intends the new one, that is the PR author's spec change, not this skill's call: report it with the test name and stop touching that check.
- **Coverage (codecov/patch)**: add tests for the uncovered lines of the PR's own diff. Never lower a threshold or a baseline.
- **Eval sample** with one or more regressions in its title: run `evals.md` for those cells (triage, then the fix) alongside the other checks' fixes; then this loop picks the PR back up to see the sample re-run. Flaky-on-retry and known failures in the title are not reds to fix. A title of `no batch produced`, or a regression whose detail says `(run error)`, is an infrastructure failure: re-run once as above, and a second one goes to triage like any red. Never edit a scenario, a judge, `standing_reds.json`, or a golden to get green outside that flow.

Fixes go in as their own commit with the repo's commit convention, the message naming the check (e.g. `fix(tests): pin negotiation tier test after rebase`). Run the relevant local check before pushing. All of a tick's fixes go out in one push, eval fixes included: each push restarts 7 to 15 minutes of CI and draws a new eval sample, so never push one fix while you are still working on another in the same tick. Never amend or rewrite the author's commits to hide a fix.

Two failed attempts on the same check means stop fixing that check and report what was tried; the loop keeps watching the others.

## 5. Things this skill does not do

Never merge (see the top). Never weaken a test, an eval, a baseline, a coverage floor, a lint rule, or a type ignore to pass. Never change product behaviour, prompts, or copy to satisfy a check, except through the eval fix in `evals.md`. Never comment on or edit the PR body, request reviewers, or re-request review. Never push to any branch but the PR's head. Never run `az`. When a decision is the author's, put it under `needs you:` and keep working what can still be worked; only when nothing is left to do until they answer does the tick end by asking it with the client's structured question tool (Claude Code: `AskUserQuestion`; otherwise a numbered list in chat with lettered options), the recommended option first.

## 6. Report

Per tick, one block per PR, short and plain:

```
<repo> #<pr> <title> — <state in reader words>
checks: <n> pass, <n> pending (<names>), <n> fail (<names>)
did: <this tick's changes: rebased onto origin/<base> (<short sha>), <old> → <new>, conflicts and how each was resolved; fix commits; re-runs; or "nothing">
tested: <what ran locally and the result; what was not run and why>        (only when this tick changed something)
evals: <the triage or fix line from evals.md>                              (only when evals ran)
needs you: <a stopped conflict, the main-checkout reset command, an author's spec call, an eval product call or standing red, a drafted Linear ticket, or nothing>
next: <when the next tick is, or "done, ready for you to merge">
```

When a PR is green and mergeable, its last line is exactly that: ready for the user to merge. The skill does not merge.
