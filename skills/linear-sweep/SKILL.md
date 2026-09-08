---
name: linear-sweep
description: Move my Linear tickets to the state their code actually reached - Merged when every PR is in development, Staging when in staging, Done when in production. Dry run first, applies only after a yes.
---

Scope: my tickets (assignee "me") in the Engineering team whose state is In Review, Merged, or Staging. `$ARGUMENTS` can widen it: `all` sweeps every assignee, a ticket key sweeps just that one, `in-progress` also includes In Progress tickets that already have a merged PR.

Truth comes from git, not from Linear's PR links. Repos and their production branch: `envoyai-backend`, `envoyai-web`, `envoyai-chrome-plugin` (`origin/production`; flow is development → staging → production), `envoyai-chat-ui` (`origin/production`). Local clones live under `~/dev/<repo>`. `git fetch origin development staging production` in each before deciding anything.

## Phase 1: dry run (always)

For each ticket:
1. Find its PRs in every repo: `gh pr list -R envoy-ai/<repo> --search "<KEY>" --state all --json number,title,state,mergedAt,mergeCommit,baseRefName,headRefName`, keep those whose title, body, or branch contains the key. Ignore promotion PRs ("promote development to staging", "Staging to prod").
2. Classify:
   - any PR still OPEN → stays where it is (say "PR #n open").
   - no PR found at all → skip with "no PR found", never guess.
   - every merged PR's `mergeCommit.oid` is an ancestor of `origin/production` (`git -C ~/dev/<repo> merge-base --is-ancestor <oid> origin/production`) → **Done**.
   - all in `origin/staging` but not all in production → **Staging**.
   - all in `origin/development` but not all in staging → **Merged**.
   - a PR merged somewhere else (a stacked branch) → leave it, say so.
3. A ticket only moves forward, never back. If Linear already shows the computed state or a later one, list it as "already right".
4. Multi-repo tickets move to the state of the repo that is furthest BEHIND (a backend PR in production with the web PR still in staging is Staging, not Done).

Print one table: key, title (short), Linear state now, computed state, evidence (the PR numbers and the branch that contains them, plus the promotion PR date for production), then the count of proposed moves. Stop and wait for "yes", a list of keys, or edits.

## Phase 2: apply (after yes)

Move each approved ticket with the Linear MCP `save_issue` (state name: Merged / Staging / Done). No comments on the issues; the state change is the record. Then print what moved and what was skipped and why.

Never touch tickets assigned to someone else unless `all` was passed. Never move to Canceled or Duplicate. Never change assignee, project, cycle, or labels.
