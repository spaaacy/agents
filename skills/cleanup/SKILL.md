---
name: cleanup
description: Clean up the local dev machine - stale git worktrees and branches, leftover local Postgres databases, and a local docker database whose Alembic state has drifted from the migration chain. Reports first, destroys only after an explicit yes.
---

Scope: $ARGUMENTS if given (`worktrees`, `databases`, `migrations`, or a repo path), else everything, for the repo in the current directory (defaults to envoyai-backend; the worktree pass also covers envoyai-web, envoyai-chrome-plugin, envoyai-chat-ui).

Flow: this is the end of the lifecycle. The worktrees it removes are the ones `/pickup`, `/address-review` and `/fix` created, and `/linear-sweep` has usually just confirmed their PRs shipped. It never touches a worktree whose PR is still being worked on in another skill without showing what is in it first.
For "wipe every worktree and sit the main checkouts on one ticket", use `/spotlight <ticket>`.

Two phases. **Phase 1 always runs and changes nothing except the step 0 sync**: gather the facts below and print one plain-English report with a numbered list of proposed actions, then ask for the approval with the client's structured question tool (Claude Code: `AskUserQuestion`; otherwise a numbered list in chat with lettered options), multi-select: one option per group that has proposals (worktrees, databases, migrations) plus "all of it", the safest set marked (Recommended), individual numbers through the free-text answer; a `down -v` is always its own option, never inside "all". **Phase 2 runs only the actions that answer approves**. Never delete a worktree with uncommitted changes, never drop a database, never `down -v`, and never force anything without that approval. Say what you did not check.

## 0. Fetch and pull latest

Do this first, for each repo in scope (backend, web, chrome-plugin, chat-ui main checkouts): `git -C <repo> fetch --prune origin`, then fast-forward the main checkout with `git -C <repo> pull --ff-only origin development`, but only when it is on `development` and `git status --porcelain --untracked-files=no` is empty (untracked files are fine). If it is on another branch, has tracked changes, or the fast-forward is refused, do not pull; note it in the report with how far behind it is (`git rev-list --count development..origin/development`). Never merge, rebase, or stash to make a pull work. Print `<repo>: <old sha> → <new sha>` per repo.

This must happen before step 3: the api container runs the backend checkout's code, so `alembic heads` only sees migrations the checkout has pulled. It also makes every "merged into origin/development" check below read the current remote.

## 1. Worktrees and branches

For each repo: `git worktree prune`, then `git worktree list --porcelain`. For every linked worktree (not the main checkout):
- Uncommitted changes? `git -C <wt> status --porcelain`. If dirty, list the files and mark it "keep unless you say otherwise"; never propose removal of a dirty tree without showing what is in it.
- Branch merged? `git merge-base --is-ancestor <branch> origin/development`, or the PR is merged/closed (`gh pr list --head <branch> --state all --json state,number`). Merged or closed with a clean tree = propose `git worktree remove <wt>` and `git branch -d <branch>`.
- Open PR or unmerged, with a clean tree = still propose `git worktree remove <wt>`; an open PR is not a reason to keep a tree, only uncommitted changes are. Keep the local branch (the PR still needs it, and it holds any commits not pushed yet), and say in the report line that the PR is open, with its number and the count of unpushed commits (`git -C <wt> rev-list --count @{u}..HEAD`; no upstream = all of them are local-only, say so).
Also list worktree directories on disk under `~/dev/.claude/worktrees/` and `<repo>/.claude/worktrees/` that git no longer knows about (leftovers after a prune); propose `rm -rf` for those.
Local branches with no worktree that are merged into origin/development: propose `git branch -d`. Never touch remote branches. Never run `git stash` (the stash stack carries other people's WIP).

## 2. Local Postgres databases

The stack is `docker compose` in the backend checkout (`docker-compose.yaml`; container `envoyai-backend-postgres-1`, user/db `envoy`). List with
`docker compose exec -T postgres psql -U envoy -d postgres -Atc "select datname, pg_size_pretty(pg_database_size(datname)) from pg_database where not datistemplate order by 1"`.

Classify:
- **Never touch**: `envoy` (the dev DB), `envoy_checkpoint` (the email agent's LangGraph checkpointer), `postgres`, `envoyai_test`.
- **Always safe to drop**: `envoy_tb_*` (per-test clones), `envoy_tpl_*` (the test template cache, rebuilt on the next DB-tier run).
- **Safe when their worktree is gone**: `envoyai_test_<slug>` where `<slug>` is the branch name lowercased with non-alphanumerics as `_` (from `poe worktree-bootstrap --with-db`); propose dropping any whose branch has no live worktree from step 1, or whose worktree step 1 proposes removing (same report, run after the worktree removal).
- **Scratch from past migration debugging**: `scratch_chain`, `envoy_mig_scratch`, `envoy_headref` and similar; propose dropping unless step 3 is about to reuse one that already sits at head.
Drop with `DROP DATABASE "<name>" WITH (FORCE);` via the same psql.

## 3. Migration state of the dev database

Run everything inside the api container; host-side `poe migration-upgrade` cannot resolve the `postgres` hostname and fails.
- Stamp: `docker compose exec -T postgres psql -U envoy -d envoy -Atc "select version_num from alembic_version"`.
- Chain head: `docker compose exec -T api alembic heads` (two heads = a fork; run `python scripts/check_single_migration_head.py` and stop, that is a branch problem not a local one).
- Is the stamp in the chain? `docker compose exec -T api alembic history | grep <stamp>`.

Then pick the case:

**a. Behind head, stamp in chain.** `docker compose exec api poetry run poe migration-upgrade`. Done.

**b. Stamp is not in the chain (orphan).** The DB was last migrated on a branch whose revision was renamed, dropped, or re-parented after merge. Find where it came from: `git log --all --oneline -S <stamp> -- alembic/versions` and read that file's `down_revision` from the commit that had it. Then `alembic stamp <down_revision>` inside the api container and `poe migration-upgrade`. If the orphan revision had created objects that a renamed successor now re-creates, the upgrade fails with "already exists"; handle per case c.

**c. Stamp equals head but the schema is missing things** (symptom: `UndefinedColumnError` on a column the model has; cause: a re-parented revision made the DB skip the revisions inserted beneath it), or case b hit "already exists". Do not do migration archaeology; diff the schema against a canonical head:
```
docker compose exec -T postgres psql -U envoy -d postgres -c "CREATE DATABASE envoy_headref OWNER envoy;"
docker compose exec -T postgres psql -U envoy -d envoy_headref -c 'CREATE EXTENSION IF NOT EXISTS "vector";' -c 'CREATE EXTENSION IF NOT EXISTS "postgis";'
docker compose exec -T -e POSTGRES_DB=envoy_headref api poetry run poe migration-upgrade
```
(reuse a scratch DB from step 2 if one is already at head). Then `comm -13` the two sides for `information_schema.columns`, `pg_indexes`, `pg_constraint`, and `pg_type where typtype='e'` plus enum labels. Missing objects map to specific revisions: repair each with `alembic stamp <parent_of_skipped>` → `alembic upgrade <skipped_revision>` → `alembic stamp head` (a plain stamp-back-and-upgrade re-runs applied revisions and errors). For a stray index, constraint, or enum value far back in the chain, apply the DDL by hand with psql instead. Extra columns or tables the dev DB has that head does not are feature-branch leftovers; leave them, SQLAlchemy only selects modeled columns. `alembic check` is not a reliable read in this repo; trust the diff. Drop `envoy_headref` when done.

**d. Wide drift or the user wants a clean slate.** Offer, never run unasked: `docker compose down`, then `rm -rf dockerdata/postgres-data` (Postgres data is a gitignored bind mount there; `docker compose down -v` only removes the datadog and logs volumes and leaves every database in place), then `docker compose up -d` recreates the DB from scratch (the api entrypoint migrates on startup, about a minute). It also wipes `envoy_checkpoint` and all local data, so say that in the offer.

Finish by re-running the stamp/head check and printing before → after.

## Report shape

```
Synced
  envoyai-backend 151fa7250 → 8bb16a997; envoyai-web up to date; envoyai-chat-ui not pulled (on aakif/x, 3 behind)
Worktrees
  keep     <path> (<branch>, dirty: 3 files)
  1. remove <path> + delete branch (<branch>, merged in #456)
  2. remove <path>, keep branch (<branch>, clean, PR #123 open, 0 unpushed)
Databases
  3. drop envoy_tb_56a04cd43b, envoy_tb_9f031974bc  (test clones)
  4. drop envoyai_test_aakif_eng_1364_7_outbox_drainer  (branch has no worktree)
Migrations
  stamp 370294b7b171 is not in the chain; head is 20260903_unusable_pw_hash_backfill
  5. stamp back to <parent> and upgrade (case b)
Not checked: <...>
Reply with the numbers to run, or "all".
```
