# Rebase (used by /fix step 3)

Bringing one PR level with its base. `/fix` has already picked where to work (its step 2) and the order (parent before child, backend before frontends). Invoking `/fix` is the go to rebase and force-push the PRs it names; nothing here merges, pushes to `development` or any branch but a PR's own head, or weakens a test, eval, golden, or coverage floor to get through.

## Fetch

`git -C <repo> fetch origin <base> <head>` for exactly the branches needed. If a fetch dies on `does not point to a valid object` for unrelated remote refs, delete only those named refs (`git update-ref -d refs/remotes/origin/<name>`), say so in the report, and fetch again; never `git gc --prune`, never touch local branches.

## Rebase and resolve

`git rebase origin/<base>` (for a stacked child, the parent's freshly pushed head). On a conflict, look at each hunk:
- **Prose and docs (CLAUDE.md, READMEs):** both sides usually changed different sentences of one long line. Keep both: take the base's version and re-insert the PR's added sentence, by script on the exact anchor text, then confirm no markers remain.
- **Imports and neighbouring code:** keep both intents when one side is the PR's change and the other an unrelated neighbour.
- **Generated files: never hand-merge.** Take the base's copy (`git checkout --ours <file>` during a rebase), regenerate from the rebased tree, stage, continue:
  - backend `openapi-v1.json`: `poe docs-export`; prompt goldens: `UPDATE_GOLDENS=1` on `tests/suite/prompts`; eval coverage matrices: `poe evals coverage --out-dir evals/harness/coverage`.
  - frontend `src/lib/generated/*`: dump the backend's schema from the backend branch the PR pairs with (rebased first in this run, else `origin/development`): `ENVIRONMENT=development <backend wt>/.venv/bin/python -c 'import json,sys; from envoy.api.app import create_app; open(sys.argv[1],"w").write(json.dumps(create_app().openapi()))' <scratch>/openapi/api/openapi.json`, serve it with `python3 -m http.server 8765` from `<scratch>/openapi`, run `PUBLIC_API_URL=http://localhost:8765 npm run generate:types`, then restore `enums.ts` and `pusher-types.ts` from HEAD when only their timestamp changed. Check the dump holds the paths the PR needs before regenerating, and stop the server afterwards.
- **Stop and report, do not guess:** both sides changed the same logic; an Alembic migration (a re-parented one needs `scripts/check_single_migration_head.py` green and is called out); a lockfile. Leave the rebase in progress, say which file and hunk, and ask with the structured question tool.

Continue with `GIT_EDITOR=true git rebase --continue` (add `-c core.hooksPath=/dev/null` when a fixer hook rewrites files mid-rebase). Every later commit can conflict again; repeat per commit.

## Check before pushing

- Generated outputs are current: re-run the generator once on the finished tree and confirm `git status` shows nothing.
- Backend: `poe evals validate` when `evals/` is in the diff, the migration-head check when a migration is, and `ENVIRONMENT=test poetry run pytest -n 8 -m unit -q <the PR's test files, plus the test files for any file where a conflict was resolved>`, all in one call (`Running checks` in `~/.agents/AGENTS.md`).
- Frontend: `npx vitest run <the PR's test files>`, and `npx svelte-check --threshold error` filtered to the PR's files (errors in untouched files are the known local Node 25 gaps; CI is the check for those).
- A failure here that the rebase caused (a test added on `development` that seeds an old type the PR changed, a scope or allow-list the PR's new import now needs) is fixed in its own commit, `fix(tests): … after rebase (<KEY>)`, never folded into the author's commits. A failure that is the PR's own spec change is reported, not fixed.

## Push

`git push --force-with-lease=<head>:<old headRefOid> origin <HEAD or branch>:<head>`, never plain `--force`, with `--no-verify` only after the checks above ran by hand (the pre-push hooks need secrets this machine lacks). Then `gh pr view` until `mergeable` is no longer UNKNOWN and confirm it is not CONFLICTING. A stacked child is rebased onto the parent's new head next, in the same run.
