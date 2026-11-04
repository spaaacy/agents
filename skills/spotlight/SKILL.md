---
name: spotlight
description: Put the main checkouts on one ticket's PR stack and remove the approved worktrees in two phases - report first, change checkouts only after the user's answer. Use for "/spotlight ENG-1234", "spotlight this ticket", "delete all worktrees and check out the stack", or "put the main branches on top of a ticket/PR".
---

Target: `$ARGUMENTS`. A Linear key (`ENG-1234`) or Linear URL; a GitHub PR URL or number also works when its title, branch or body names the ticket. Nothing means ask which ticket with the client's structured question tool (Claude Code: `AskUserQuestion`; otherwise a numbered list in chat with lettered options) and stop.

Scope: the main checkouts at `~/dev/envoyai-backend`, `~/dev/envoyai-web`, `~/dev/envoyai-chrome-plugin` and `~/dev/envoyai-chat-ui`. Include `~/dev/ci-workflows` only when the ticket's PRs or stack touch it. Resolve these to absolute paths once; every git command uses `git -C <absolute-repo-or-worktree>`, every worktree path is absolute, and parallel shell calls never use a bare `cd`.

Flow: this puts work `/pickup` already opened in front of you in the main checkouts. It can remove every linked worktree, including other tickets' work, but only after showing what each holds and getting the user's choice. `/cleanup` owns leftover-directory and database cleanup, with one exception here: a local dev database that is only behind the backend checkout's new migrations, which this skill brings forward when the answer approves it (a switch is what leaves it behind, and the api then refuses to boot). Everything messier is `/cleanup migrations`. `/fix` owns keeping the PRs green; this skill does not.

Two phases. **Phase 1 always runs first**: read the stack and local state, changing nothing except `git -C <repo> fetch --prune origin` and the requested `git -C <repo> worktree prune` metadata cleanup. Read-only reads of the local docker database are allowed. No pull, checkout, worktree removal, migration or file edit. Print the report, then ask ONE approval question with the client's structured question tool (Claude Code: `AskUserQuestion`; otherwise a numbered list in chat with lettered options). **Phase 2 runs only what that answer approves**. A request to "delete all worktrees" still gets the inventory first. Say what you did not check.

# Phase 1: inspect

## 1. Resolve the ticket and stack

Read the key from the argument, normalizing it to uppercase; a Linear URL needs no Linear call. For a PR URL, use its named repository; for a bare number, use the current repo only if it is one of the repos in scope, otherwise ask which repository and stop. Read `gh pr view <number> -R envoy-ai/<repo> --json number,title,body,headRefName,baseRefName,state,url`. Extract the ticket key from title, branch and body; if several different keys appear or none does, ask which ticket and stop. Never guess from a PR number that happens to match a ticket number. Never call Linear.

In every repo, search:

```
gh pr list -R envoy-ai/<repo> --search "<KEY>" --state all --limit 1000 --json number,title,body,state,headRefName,baseRefName,headRefOid,url
gh pr list -R envoy-ai/<repo> --state open --limit 1000 --json number,title,body,state,headRefName,baseRefName,headRefOid,url
```

Search `ci-workflows` too to determine whether it belongs in scope; without a matching PR or stack dependency, leave its checkout and worktrees alone. Keep search results only when their title, body or head branch carries the exact key, case-insensitively (`ENG-1234` must not match `ENG-12345`). The open list also catches branch-only matches and children that never name the key. Ignore promotion and release PRs: development → staging → production, and release-please branches whose changelog merely repeats the key. Do not trust a search hit based only on comments or a PR number. If a list reaches its limit, paginate before claiming completeness; a failed or incomplete read leaves that repo unresolved, not "no PRs".

Follow the stack in both directions, recursively, including merged PRs: a PR's feature-branch base points to the parent PR's head; PRs based on its head are children. Query missing parents with `gh pr list -R envoy-ai/<repo> --head <base> --state all --limit 1000 --json ...` and children with `gh pr list -R envoy-ai/<repo> --base <head> --state all --limit 1000 --json ...` (the same fields above). Deduplicate by repo and PR number. Stop at `development`, `staging`, `production`, `main`, `master` or the repo's default branch; sharing one of those bases does not put every PR in the stack. A cross-repo PR link can establish a dependency, but does not make an unrelated ticket part of this one without that evidence.

Per repo, choose the highest **OPEN** PR's head: the open PR with no other open PR above it in that chain. A merged child does not outrank an open parent. If all the stack's PRs are merged, or the repo has no matching PRs, target `development`. Multiple open tips in separate chains or a fork are ambiguous; show them and leave that repo unresolved until the user names one in the approval's free-text answer. Closed-but-unmerged work with no open tip is unresolved too, never silently treated as merged. Name the selected PR and any related-ticket branches included in its stack.

## 2. Fetch and inventory every worktree

For each repo in scope, run `git -C <repo> fetch --prune origin`, then `git -C <repo> worktree prune`, then `git -C <repo> worktree list --porcelain`. A fetch failure means the target SHA is unverified and that repo cannot switch in phase 2. Prune removes stale registrations only; do not remove directories left on disk. Inventory every linked worktree, not just paths under one expected worktree directory, and do not count the main checkout as a linked worktree.

For each linked worktree, record:

- Absolute path, branch (or detached HEAD and SHA), and whether it belongs to the ticket. Match its branch to a resolved stack head or an exact key in the branch name; distinguish a related ticket's stack branch from a direct match. Do not infer ownership from a nearby directory name. Mark anything outside the resolved stack **OTHER TICKET / NOT THIS TICKET'S WORK**; uncertain ownership gets the same warning.
- Uncommitted change count: `git -C <wt> status --porcelain` (include untracked files). Count status entries, not lines in a filename; `-z` is safe for parsing. For a dirty tree, also show the changed paths before asking for approval.
- Unpushed commit count: `git -C <wt> rev-list --count '@{u}..HEAD'`. Check the tracking ref exists first; no upstream or a missing ref is **NO UPSTREAM — not verified pushed**, never zero. A detached worktree has the same protection. A failed status or commit read is unknown, never clean.

Flag uncommitted work and unpushed commits loudly on the worktree's line. A clean tree can still hold unpushed work. Record the HEAD and status now so phase 2 can spot changes after approval.

## 3. Inventory the main checkouts

For each main checkout, read its branch, HEAD, and tracked changes with `git -C <repo> status --porcelain --untracked-files=no`. Untracked files are fine and carried along; never remove them to make a switch work. Record the target branch and full fetched remote SHA (`git -C <repo> rev-parse 'refs/remotes/origin/<target>^{commit}'`), displaying a short SHA in the report. For an open PR, confirm that SHA matches its head; if the PR moved during the read, refresh it before reporting.

If a local target branch exists, compare it with its fetched remote using `git -C <repo> rev-list --left-right --count <target>...origin/<target>` (local-only, remote-only). With local-only commits, the remote SHA is not necessarily where it would land: show the local SHA and both counts, and refuse that repo unless an ordinary fast-forward can reach the reported target. Never silently discard local commits or promise a remote SHA when the local branch is ahead. Check the feature branch tracks `origin/<target>`; a different configured upstream is a refusal, not permission to pull some other branch or reconfigure it.

**The backend's local database.** Read it from the docker stack, which is local by construction; never through the `pg-readonly` MCP or any configured connection, which point at production or staging. With `B=<absolute backend path>`:

- Stamp: `docker compose -f $B/docker-compose.yaml exec -T postgres psql -U envoy -d envoy -Atc "select version_num from alembic_version"`.
- Is the stamp in the target's chain: `git -C $B grep -l "<stamp>" <target-sha> -- alembic/versions` (a hit means the target has that revision; no hit means a revision only another branch has, or one renamed since).
- Is the api up: `docker compose -f $B/docker-compose.yaml ps api` for its health, and `docker compose -f $B/docker-compose.yaml logs --tail 50 api` for a schema refusal ("Database schema is BEHIND this build"). An api that already refuses to boot was behind before this switch; say so, it is not this switch's doing.

The stack not running, or a read that fails, is **local database not read**, never "current". Whether the target's head is ahead of the stamp is decided in phase 2, inside the api container, once the checkout holds the target's migrations.

## 4. Report, ask once, wait

Answer first: `<KEY> — <n> linked worktrees, <n> this ticket's; <repo> targets <branch> (#<pr>), ...`. One line per worktree and one per main checkout, plus changed-file details where needed:

```
Worktrees
  <absolute path> | <branch>@<sha> | THIS TICKET | changes: 0 | unpushed: 0
  <absolute path> | <branch>@<sha> | OTHER TICKET — NOT THIS TICKET'S WORK | changes: 0 | unpushed: 0
  <absolute path> | <branch>@<sha> | OTHER TICKET | WARNING: 2 changes, 1 unpushed — KEEP unless named explicitly
    changed: <paths>
Main checkouts
  <repo>: <current>@<sha> → <target>@<sha> (#<pr> or all merged / no PRs); tracked changes: <n>; <refusal if any>
Local database
  stamp <revision> | in the target's chain | not in the target's chain → /cleanup migrations | not read: <why>
  api: healthy | refusing to boot: <the log line> | not running
Not checked: <unread repos, dependencies, other omissions>
```

Then ONE `AskUserQuestion` call. Its first question, single-select: **remove all worktrees**, **only this ticket's**, or **stop**. Each removal option also means move the main checkouts to the reported targets where safe; say that in its description. Put the recommended option first and mark it `(Recommended)`: "remove all worktrees" only when ALL are verified clean and pushed; otherwise recommend "only this ticket's" if that removes a verified-clean, pushed set, or "stop" if no such set exists. Always call out that "all" includes other tickets' work.

When the local database's stamp was read and is in the target's chain, the same call carries a second question: **bring the local database up to the target's migrations if it is behind** (Recommended: forward only, inside the api container, then the api restarts on its own) or **leave it**. Its options say that "leave it" can leave the api refusing to boot. No second question when the stamp is not in the target's chain, unreadable, or the stack is down; say `/cleanup migrations` in the report instead.

Neither broad option approves deleting a tree with uncommitted or unpushed work, no upstream, or unreadable state. Those stay unless the free-text answer explicitly names each path or uniquely identified branch after seeing its warning. Unknown state must become readable before any removal. Free text can also choose an ambiguous target or explicitly request a branch deletion. Keep local branches by default. Wait for the answer; do not run phase 2 while the question is pending. An explicit phase-1-only dry run ends with the proposed question recorded in its report, without presenting a destructive approval or running phase 2.

# Phase 2: apply the answer

## 1. Remove the approved worktrees first

Re-read each approved worktree's HEAD, changes and unpushed count. If it gained work or changed since the warning and approval, keep it and report why; approval for the old inventory is not approval for new work. "Only this ticket's" includes only paths the report explicitly classified as belonging to this ticket's resolved stack. Other trees stay.

Remove each approved path with `git -C <repo> worktree remove <absolute-path>`. **Never `--force`, never `rm -rf`.** An explicitly named dirty tree can still be refused by ordinary git removal; report that refusal and leave it, never clean it up to make the command succeed. Locked or unreadable worktrees stay. Finish removals before any checkout switch, because git refuses to check out a branch another worktree holds. A retained worktree holding the target means that main checkout is refused; never bypass the lock or choose another target.

Keep local branches unless the answer explicitly asked to delete one. Even then, prove the branch is fully contained in its existing remote tracking ref with `git -C <repo> merge-base --is-ancestor <branch> <remote-tracking-ref>`, then use `git -C <repo> branch -d <branch>` only. No remote ref, unpushed commits, a still-checked-out branch, a selected target branch, or a refused `-d`: keep it and say why. Never `-D`, and never delete a remote branch. Say which local branches were deleted.

## 2. Move each main checkout

Re-read the main checkout's HEAD and tracked status immediately before switching. Refuse and report tracked changes, an unresolved target, an unverified remote, or a checkout changed since the report; do not fix them. Never stash, never `reset --hard`, never rebase, never force a switch. Untracked files stay; if they would be overwritten, let git refuse and report it.

- Feature branch already local: `git -C <repo> switch <branch>`, then `git -C <repo> pull --ff-only`.
- Feature branch missing locally: `git -C <repo> switch -c <branch> --track origin/<branch>`, then `git -C <repo> pull --ff-only`.
- Development: `git -C <repo> switch development && git -C <repo> pull --ff-only origin development`.

Run the pull only after a successful switch. If a local feature branch has no upstream, report and refuse rather than guessing or changing configuration. If a pull refuses to fast-forward, read `git -C <repo> rev-list --left-right --count HEAD...origin/<target>` and report how many commits are local-only and remote-only, plus where the checkout actually stopped. An ahead-only local target also cannot land on the fetched remote by fast-forward; refuse it. Never force or repair divergence. A pull can fetch a newer remote tip than the preview; show the actual final SHA.

## 3. Report what needs installing or migrating

Compare the old and actual final SHA, not just branch names. On web and chrome-plugin, `git -C <repo> diff --name-only <old-sha> <new-sha> -- package-lock.json`: if changed, say `npm ci` is needed; do not run it. On backend, the same check for `poetry.lock`: say it changed, never run `poetry install`.

Then the backend's local database, from the docker stack only (same reads as phase 1; never a configured connection, which is production or staging). Run this whether or not the backend's SHA moved: the database can already be behind, as an api refusing to boot in phase 1 shows. With `B=<absolute backend path>` and the stamp re-read now:

- `docker compose -f $B/docker-compose.yaml exec -T api alembic heads`. Two heads is a branch problem, not a local one: refuse, name `python scripts/check_single_migration_head.py`, change nothing.
- `docker compose -f $B/docker-compose.yaml exec -T api alembic history -r <stamp>:heads`. It lists the revisions the database lacks; empty means current; an error means the stamp is not in this chain.
- **Behind, and the answer approved the upgrade:** `docker compose -f $B/docker-compose.yaml exec -T api poetry run poe migration-upgrade` (never from the host: it cannot resolve the `postgres` hostname). Then re-read the stamp, and confirm the api answers: `curl -s -o /dev/null -w '%{http_code}' --max-time 5 localhost:8000/api/openapi.json` returns 200 within about two minutes and `docker compose -f $B/docker-compose.yaml ps api` reads healthy. The api's code reload never re-runs migrations, which is why a switch leaves it refusing to boot. If the old branch lacks revisions the upgrade applied, say that switching back to it will leave the database ahead of that code.
- **Behind and not approved, not in the chain, ahead of the code, a fork, or the stack down:** change nothing, report what you read, and name `/cleanup migrations`.

Never stamp, downgrade, wipe, `docker compose down -v`, start a stack that is not running, or run `az`. A failed upgrade is reported with its error and left for `/cleanup migrations`; never repair it here.

## 4. Final report

Answer first: `<KEY> — removed <n> worktrees; moved <n> main checkouts; <n> refused.` Then one line per repo, including refused repos at their actual final position:

```
<repo>: <old branch>@<sha> → <new branch>@<sha>; <lockfile notice or refusal>
Removed: <absolute worktree paths>
Branches deleted: <names, or none; kept by default>
Local database: <stamp before> → <stamp after> (upgraded | current | left: <why>); api <healthy | refusing to boot | not running>
Refused / kept: <paths or repos and reasons>
Not checked: <dependencies, unavailable reads>
```

Stop there. Never touch Linear, push, merge, create or edit PRs, repair failing checks, or clean up databases beyond the approved forward upgrade. This skill changes local checkouts, approved worktrees, and, when approved, the local database's migrations only.
