---
name: rebase
description: Bring a ticket's or PR's branches fully up to date with the latest origin/development - rebase every open PR in the stack across repos, resolve conflicts, regenerate generated files from the rebased tree, run the touched tests, and push with --force-with-lease, then hand the PRs to /watch. Use when the user says rebase, bring up to date, catch up with development, or "do the same" after a merge for a ticket (ENG-1234) or a PR.
---

Target: `$ARGUMENTS`. A Linear key (`ENG-1234`) or Linear URL means every open PR for that ticket across the repos; a GitHub PR URL or number means that PR and the open PRs stacked on it. A bare number is looked up in the current repo first, then `envoyai-web`, `envoyai-chrome-plugin`, `envoyai-chat-ui` (backend PRs are in the 2000s, the others are lower; never guess across repos, ask if two match). Nothing means the open PR for the current branch; no PR means ask which with the client's structured question tool and stop. A trailing `watch` (or "and /loop /watch it") means start the watch loop on every pushed PR at the end.

Invoking this skill is the go to rebase and force-push the PRs it names. It never merges, never pushes to `development` or any branch but a PR's own head, never runs `az`, and never weakens a test, eval, golden, or coverage floor to get through.

Flow: `/pickup` opened the PRs; this brings them level with `development` after a sibling merges or the base moves; `/watch <pr>` then keeps them green (a red `Eval sample` goes to `/fix-evals`, a coverage or test red is the watcher's); the merge is the user's.

## 1. Resolve the PRs

Resolve the ticket to its PRs and stack the way `/spotlight` step 1 does (exact-key search in every repo, follow bases and children, skip promotion and release PRs). Keep only OPEN PRs; a merged one is already in `development`. Order them so a PR is rebased after the PR it depends on: within a repo, parent before child; across repos, the backend before any frontend whose generated types come from it. For each PR record repo, number, head branch, base, `headRefOid`, `mergeable`, `mergeStateStatus`. A PR already level with its base (`git merge-base --is-ancestor origin/<base> origin/<head>`) needs nothing; say so and skip it.

## 2. Fetch and pick where to work

`git -C <repo> fetch origin <base> <head>` for exactly the branches needed. If a fetch dies on `does not point to a valid object` for unrelated remote refs, delete only those named refs (`git update-ref -d refs/remotes/origin/<name>`), say so in the report, and fetch again; never `git gc --prune`, never touch local branches.

Work in a worktree, never by switching the main checkout:
- A linked worktree already on the head branch: use it if `git status --porcelain --untracked-files=no` is clean; dirty means report and skip that PR.
- The main checkout is on the head branch: do not rebase there (the permission classifier refuses it, and it is the user's working copy). Make a detached worktree at `~/dev/.claude/worktrees/<repo-short>-<branch-slug>` from `origin/<head>`, rebase there, and push with `HEAD:<head>`. Tell the user their main checkout is now behind the remote and give the one command that resets it (`git -C <repo> reset --hard origin/<head>`), only after checking it has no uncommitted changes; never run it for them.
- Otherwise `git worktree add -B <head> <path> origin/<head>`.

Setup per repo: backend runs `<shared venv>/bin/python -m scripts.dev.worktree_bootstrap` once (never `poetry install`, never `docker compose`); frontends get `node_modules` as an APFS clone of the main checkout's (`cp -Rc`), not a symlink, because vite refuses files reached through a symlink outside the worktree. Never `git stash`.

## 3. Rebase and resolve

`git rebase origin/<base>` (for a stacked child, the parent's freshly pushed head). On a conflict, look at each hunk:
- **Prose and docs (CLAUDE.md, READMEs):** both sides usually changed different sentences of one long line. Keep both: take the base's version and re-insert the PR's added sentence, by script on the exact anchor text, then confirm no markers remain.
- **Imports and neighbouring code:** keep both intents when one side is the PR's change and the other an unrelated neighbour.
- **Generated files: never hand-merge.** Take the base's copy (`git checkout --ours <file>` during a rebase), regenerate from the rebased tree, stage, continue:
  - backend `openapi-v1.json`: `poe docs-export`; prompt goldens: `UPDATE_GOLDENS=1` on `tests/suite/prompts`; eval coverage matrices: `poe evals coverage --out-dir evals/harness/coverage`.
  - frontend `src/lib/generated/*`: dump the backend's schema from the backend branch the PR pairs with (rebased first in this run, else `origin/development`): `ENVIRONMENT=development <backend wt>/.venv/bin/python -c 'import json,sys; from envoy.api.app import create_app; open(sys.argv[1],"w").write(json.dumps(create_app().openapi()))' <scratch>/openapi/api/openapi.json`, serve it with `python3 -m http.server 8765` from `<scratch>/openapi`, run `PUBLIC_API_URL=http://localhost:8765 npm run generate:types`, then restore `enums.ts` and `pusher-types.ts` from HEAD when only their timestamp changed. Check the dump holds the paths the PR needs before regenerating, and stop the server afterwards.
- **Stop and report, do not guess:** both sides changed the same logic; an Alembic migration (a re-parented one needs `scripts/check_single_migration_head.py` green and is called out); a lockfile. Leave the rebase in progress, say which file and hunk, and ask with the structured question tool.

Continue with `GIT_EDITOR=true git rebase --continue` (add `-c core.hooksPath=/dev/null` when a fixer hook rewrites files mid-rebase). Every later commit can conflict again; repeat per commit.

## 4. Check before pushing

- Generated outputs are current: re-run the generator once on the finished tree and confirm `git status` shows nothing.
- Backend: `poe evals validate` when `evals/` is in the diff, the migration-head check when a migration is, and `ENVIRONMENT=test poetry run pytest -m unit -q <the PR's test files and the suites of its touched modules>`. Every `ENVIRONMENT=test` run consumes `secrets/test.json` on this machine, so seed it from `secrets/development.json` plus `API_URL`/`APP_URL` and keep a background re-copy loop alive for the duration, then delete it.
- Frontend: `npx vitest run <the PR's test files>`, and `npx svelte-check --threshold error` filtered to the PR's files (errors in untouched files are the known local Node 25 gaps; CI is the check for those).
- A failure here that the rebase caused (a test added on `development` that seeds an old type the PR changed, a scope or allow-list the PR's new import now needs) is fixed in its own commit, `fix(tests): … after rebase (<KEY>)`, never folded into the author's commits. A failure that is the PR's own spec change is reported, not fixed.

## 5. Push

`git push --force-with-lease=<head>:<old headRefOid> origin <HEAD or branch>:<head>` with `--no-verify` only after the checks above ran by hand (the pre-push hooks need secrets this machine lacks). Then `gh pr view` until `mergeable` is no longer UNKNOWN and confirm it is not CONFLICTING. A stacked child is rebased onto the parent's new head next, in the same run.

## 6. Report, then watch

One block per PR:

```
<repo> #<pr> <title> — <level with development | rebased and pushed | skipped: why | stopped: why>
did: rebased onto origin/<base> (<short base sha>); <old> → <new>; conflicts: <files and how each was resolved, or none>
checks: <what ran locally and the result; what was not run and why>
needs you: <a stopped conflict, the main-checkout reset command, or nothing>
```

Then, when the user asked for it (or always, if the PRs were already being watched), schedule the watch: one `ScheduleWakeup` whose prompt is `/watch /loop <every pushed PR URL>` plus, for any PR pushed from a detached worktree, its path and `push with HEAD:<head>`, about 10 minutes out.
