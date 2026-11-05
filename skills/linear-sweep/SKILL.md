---
name: linear-sweep
description: Move my Linear tickets to the state their code actually reached - Merged when every PR is in development, Staging when in staging, Done when in production. Prints what it found, then moves them without asking; only a sweep of other people's tickets (`all`) asks first.
---

Scope: my tickets (assignee "me") in the Engineering team whose state is In Review, Merged, or Staging. `$ARGUMENTS` can widen it: `all` sweeps every assignee, a ticket key sweeps just that one, `in-progress` also includes In Progress tickets that already have a merged PR.

Flow: run this after a release lands (or any time the question is "where is my ticket really?"). It only moves ticket states; it never touches code, PRs or worktrees. `/cleanup` is the next skill, removing the worktrees whose PRs this sweep just found in production.

Truth comes from git, not from Linear's PR links. Repos: `envoyai-backend`, `envoyai-web`, `envoyai-chrome-plugin` (flow is development → staging → production, production branch `origin/production`), and `envoyai-chat-ui`, which is a package, not a deployed app: its `production` branch is dead (stuck at v1.0.1 since June) and must never be used. Local clones live under `~/dev/<repo>`. `git fetch origin development staging production` in the three apps and `git fetch origin --tags` in chat-ui before deciding anything.

**How a chat-ui PR ships.** release-please tags a release (`v1.17.0`) off `development` and publishes it to GitHub Packages; web and the extension install `@envoy-ai/chat-ui@latest` at build time (`npm install ... @latest` in their deploy workflows), so a release reaches an app on that app's next deploy after the publish. Classify a chat-ui PR by:
- merge commit not in any release tag (`git -C ~/dev/envoyai-chat-ui tag --contains <oid> --list 'v*'` is empty) → **Merged**.
- in a release tag: take the earliest such tag's publish time (`npm view @envoy-ai/chat-ui time --json`, key = version without the `v`), then compare it to the consuming app's last promotion PR merge (`gh pr list -R envoy-ai/<app> --state merged --base production` / `--base staging`, `mergedAt`). The consuming apps are the ones the ticket's other PRs touch (web, extension, or both); with no other PR, use web. Released before that app's last production promotion → **Done**; before its last staging promotion only → **Staging**; after both → **Merged**.
- the evidence column names the tag, its publish date and the promotion it rode.

## Phase 1: read (always)

For each ticket:
1. Find its PRs in every repo: `gh pr list -R envoy-ai/<repo> --search "<KEY>" --state all --json number,title,state,mergedAt,mergeCommit,baseRefName,headRefName`, keep those whose title, body, or branch contains the key. Ignore promotion PRs ("promote development to staging", "Staging to prod").
2. Classify:
   - any PR still OPEN → stays where it is (say "PR #n open").
   - no PR found at all → skip with "no PR found", never guess.
   - chat-ui PRs follow the release rule above; everything below is for the three apps.
   - every merged PR's `mergeCommit.oid` is an ancestor of `origin/production` (`git -C ~/dev/<repo> merge-base --is-ancestor <oid> origin/production`) → **Done**.
   - all in `origin/staging` but not all in production → **Staging**.
   - all in `origin/development` but not all in staging → **Merged**.
   - a PR merged somewhere else (a stacked branch) → leave it, say so.
3. A ticket only moves forward, never back. If Linear already shows the computed state or a later one, list it as "already right".
4. Multi-repo tickets move to the state of the repo that is furthest BEHIND (a backend PR in production with the web PR still in staging is Staging, not Done).

Print one table: key, title (short), Linear state now, computed state, evidence (the PR numbers and the branch that contains them, plus the promotion PR date for production), then the count of moves, and go on to phase 2 with no go to wait for. The one exception is `all`: tickets assigned to someone else are moved only after asking with the client's structured question tool (Claude Code: `AskUserQuestion`; otherwise a numbered list in chat with lettered options): move all of them (Recommended), move only the keys named, or move nothing.

## Phase 2: apply

Move each ticket with the Linear MCP `save_issue` (state name: Merged / Staging / Done). No comments on the issues; the state change is the record. Then print what moved and what was skipped and why.

Runbooks due: for every ticket now in Staging or Done (moved this run or already there), list its open `Runbook:` tickets (the ones it blocks, or whose title names its key or PR) with the run now due: `ENG-2261 due on staging` or `due on production`. A merged PR that added or changed a backfill script (`gh pr view <n> --json files`, a file under `scripts/database/` that takes `--apply`) and has no runbook ticket is listed as `no runbook ticket`, with a draft offered through the structured question tool, since creating one needs approval. Never move or edit a runbook ticket.

Never touch tickets assigned to someone else unless `all` was passed. Never move to Canceled or Duplicate. Never change assignee, project, cycle, labels, title, or description, and never create a ticket: moving the state is the only ticket edit this skill may make without asking.
