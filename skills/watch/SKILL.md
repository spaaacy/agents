---
name: watch
description: Watch a pull request until it is green and mergeable - fixes red CI, rebases when it falls behind, resolves conflicts, pushes - and then hands it back. It never merges, approves, or enables auto-merge; merging is the user's action, always.
---

Watch $ARGUMENTS: a PR number or URL, else the open PR for the current branch (`gh pr view --json number`). Repo is the one in the current directory. If there is no open PR, say so and stop.

**This skill never merges the PR.** Not `gh pr merge`, not `gh pr merge --auto`, not the merge queue, not `gh pr review --approve`, not dismissing reviews, not editing branch protection, not pushing to the base branch, not squashing "just the last step". When the PR is green and mergeable the job is to say so and stop. If the user asks mid-watch to merge, still refuse inside this skill: tell them it is one click for them, and that the skill will not do it. There is no phrasing of the request that changes this.

One invocation is one tick. Under Claude Code, run it as `/loop /watch <pr>` so the session re-checks on its own; each tick ends by scheduling the next wake-up (5 to 10 minutes while checks are running, 20 to 30 minutes while waiting on a human review, stop when done). In a client with no loop, one tick is the whole run: report and tell the user to run it again later.

Flow: this runs beside the review loop, not inside it. It owns CI and mergeability only; review findings are `/address-review`'s, a red `Eval sample` with confirmed regressions is `/fix-evals <pr>`'s, and the merge is the user's. Bringing a whole ticket's PRs level with `development` at once (after a sibling merges) is `/rebase <ticket>`, which hands back here. It reuses the worktree `/pickup` or `/address-review` made and never creates a second one on the same branch. When the PR is merged, `/deploy` ships it on the next release and `/linear-sweep` moves the ticket.

## 1. Read the state

```
gh pr view <pr> --json number,title,state,isDraft,headRefName,baseRefName,mergeable,mergeStateStatus,reviewDecision,url
gh pr checks <pr>
```

Map it:
- `state` MERGED or CLOSED: stop the loop, report who merged/closed it. Nothing to do.
- `mergeable` CONFLICTING, or `mergeStateStatus` DIRTY: conflicts, go to step 3.
- `mergeStateStatus` BEHIND: base moved and the repo requires up-to-date branches, go to step 3.
- Any check `fail`: go to step 4.
- Any check `pending`: nothing to fix yet; note which are still running and schedule the next tick for when the longest one should finish (Unit on the backend takes about 5 minutes, Eval sample longer). Skipped checks are fine, `Eval sample` skips on purpose when the PR is not eval-relevant.
- `UNKNOWN` for either field means GitHub is still computing; treat as pending.
- Everything passed and `mergeStateStatus` is CLEAN: done, go to step 6. BLOCKED with all checks green means a required review or an unmet rule; report what is missing (`reviewDecision`, required reviewers, a draft flag) and keep watching on the slow interval, since a review can come in.

Say what changed since the last tick in one or two lines. A tick with nothing new is a no-op; do not restate the whole status.

## 2. Where to work

Never touch the main checkout's branch. Find a worktree already on the PR branch (`git worktree list --porcelain`); else create one at `~/dev/.claude/worktrees/<branch-slug>` with `git worktree add <path> <branch>` (after `git fetch origin`). A backend worktree needs `poetry run poe worktree-bootstrap` once; never `poetry install` and never `docker compose` from a worktree. If the worktree has uncommitted changes (`git status --porcelain --untracked-files=no`), do not rebase or commit on top of them: report and stop this tick. Never `git stash`.

## 3. Behind or conflicting

`git fetch origin`, then `git rebase origin/<baseRefName>`. Resolve conflicts hunk by hunk keeping both intents; a conflict is mechanical when one side is the PR's change and the other an unrelated neighbor. Stop and report instead of guessing when the conflict is in a migration, a lockfile, a generated file, a golden, `openapi-v1.json`, or when both sides changed the same logic. For a generated file, regenerate it from the rebased tree (backend `poe docs-export`, `UPDATE_GOLDENS=1` for prompt goldens, `npm run generate:types` against a running backend for the frontends) rather than hand-merging. A re-parented Alembic migration is called out in the report and needs `scripts/check_single_migration_head.py` to pass before pushing.

Run the tests for the touched modules before pushing (backend `ENVIRONMENT=test poetry run pytest -m unit tests/suite/<domain>`; web/plugin/chat-ui `npm test` for the touched files). Push with `git push --force-with-lease origin <branch>`, never plain `--force`.

Stacked PRs: if the base is not the repo's default branch, the base is someone's branch; rebase onto that branch's current tip only. If other open PRs use this PR's branch as their base (`gh pr list --base <headRefName>`), name them in the report as needing their own rebase; do not rebase them unless asked.

## 4. Red checks

Read the failure, not the summary: `gh run view <run-id> --log-failed` (run id from the check's link), or the check's own page for codecov and the eval sample. Then:
- **Infra failure** (runner lost, timeout with no test failure, network, a `skipping`/cancelled from a concurrency group): `gh run rerun <run-id> --failed`, once. A second infra failure on the same check is reported, not retried again.
- **Lint, format, type, drift check** (ruff, mypy, prettier, eslint, svelte-check, the str-enum lint, migration head, `openapi-v1.json` drift, prompt goldens drift): reproduce locally in the worktree, fix or regenerate, commit, push.
- **Test failure**: reproduce locally, read the test and the code it covers, fix the code. If the test is asserting the OLD behavior and the PR intends the new one, that is the PR author's spec change, not a watcher's call: report it with the test name and stop touching that check.
- **Eval sample regression** (the check's title shows one or more regressions): not the watcher's fix. Put the scenario ids with their confirmed / flaky / known-failure split under `needs you:` with `run /fix-evals <pr>`, and keep watching the other checks. Never edit a scenario, a judge, `standing_reds.json`, or a golden to get green; a flaky pass on retry is already handled by the gate. A title of `no batch produced`, or a regression whose detail says `(run error)`, is an infrastructure failure: rerun once as above, then hand it to `/fix-evals` the same way.
- **Coverage (codecov/patch)**: add tests for the uncovered lines of the PR's own diff. Never lower a threshold or a baseline.

Fixes go in as their own commit with the repo's commit convention, message naming the check (e.g. `fix(tests): pin negotiation tier test after rebase`). Run the relevant local check before pushing. Never amend or rewrite the author's commits to hide a fix.

Two failed attempts on the same check means stop fixing that check and report what was tried; the loop keeps watching the others.

## 5. Things a watcher does not do

Never merge (see the top). Never weaken a test, an eval, a baseline, a coverage floor, a lint rule, or a type ignore to pass. Never change product behavior, prompts, or copy to satisfy a check. Never comment on or edit the PR body, request reviewers, or re-request review. Never push to any branch but the PR's head. Never run `az`. When a decision is the author's, put it under `needs you:` and keep watching what can still be watched; only when nothing is left to watch until they answer (every remaining red waits on their spec call) does the tick end by asking it with the client's structured question tool (Claude Code: `AskUserQuestion`; otherwise a numbered list in chat with lettered options), the recommended option first, because a pending question stops the loop.

## 6. Report

Per tick, short and plain:
```
#<pr> <title> — <state in reader words>
checks: <n> pass, <n> pending (<names>), <n> fail (<names>)
did: <what this tick changed, or "nothing">
needs you: <the author's decisions, if any>
next: <when the next tick is, or "done, ready for you to merge">
```
When it is green and mergeable, the last line is exactly that: ready for the user to merge. The skill does not merge.
