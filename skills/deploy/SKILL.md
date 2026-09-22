---
name: deploy
description: Run the daily on-call deploy across envoyai-backend, envoyai-web and envoyai-chrome-plugin - cut the release-please releases, promote development to staging, open the staging to production PRs, wait for every build and deploy run, merge to production, verify. It reads on its own but changes nothing without an explicit GO from the user for that exact action. Use when the user says deploy, promote, release, or ship to staging or production.
---

Deploy $ARGUMENTS: nothing means all three repos; `--only backend|web|plugin` limits it to one. Repos are `envoy-ai/envoyai-backend`, `envoy-ai/envoyai-web`, `envoy-ai/envoyai-chrome-plugin`, always addressed with `-R`; this skill needs no local checkout and never touches a working tree.

**Rule zero: nothing changes without a GO.** On its own this skill only reads: `gh pr view/list`, `gh run list/view`, `gh api` reads, `curl` of the two version endpoints. Every change (a merge, a workflow run, opening a PR, a Slack message) is proposed first as an exact list, for example "merge backend #2535 (squash), web #705 (squash), plugin #253 (squash)", and executed only after the user replies GO to that list. A GO covers that list and nothing else; the next stage asks again. There is no flag that removes the asking and no session-wide grant. If the user says "just do the whole thing", say the skill asks per stage and continue asking. Never `az`, in any form. Never merge with `--admin`.

**Approvals.** An approval through this skill is the user's own GitHub account stamping, so it is an ordinary change: proposed, then done on GO. The skill may approve a PR the user did not author (the release-please PRs, the automation-opened promotion PRs). It cannot approve the production PRs it opens in step 3: GitHub rejects self-approval, and the production ruleset wants one approval from someone other than the merger, so those wait for a teammate.

Under Claude Code, run it as `/loop /deploy` so the session re-checks on its own. Each tick re-reads GitHub, works out the stage each repo is in, then either proposes the next change and stops for GO, or has nothing to change and says what it is waiting on and when it checks again. A tick with nothing new is a one-liner. In a client without a loop, one tick is the whole run: report and tell the user to run it again.

## The pipeline, per repo

- `development` -> squash-merge the release-please PR -> tag + GitHub Release -> a `development -> staging` PR is opened by automation -> merge commit -> staging builds and deploys -> `staging -> production` PR -> one human approval + release gate -> merge commit -> production builds and deploys.
- Backend: push to `staging`/`production` runs `build-and-publish.yml` (api, worker, livekit images), then `deploy.yml` via `workflow_run` (API blue-green with a health gate and auto-rollback to blue, schema self-check, worker, beat, LiveKit ACA, LiveKit Cloud). 15 to 18 minutes.
- Web: push runs `deploy.yml` (Cloudflare Worker). Under 3 minutes.
- Plugin: push runs `branch-build.yml` (zips + rolling `latest-<env>` pre-release); push to `production` also runs `store-publish.yml` (Chrome Web Store upload + submit, only when `package.json` version changed; cancels any submission still in review).
- Merge methods are fixed by rulesets: `development` accepts squash; `staging` and `production` accept merge commits only. Production needs one approval from someone other than the merger; backend and web production also require `gate / Release cut` (manifest version on staging differs from production) and `Integration`. The plugin's production checks ruleset is disabled: approval only.

## 0. Preflight

Read, then one GO.

```
gh auth status
gh pr list -R envoy-ai/<repo> --base production --state open --json number,title,headRefName,createdAt
gh pr list -R envoy-ai/<repo> --base staging --state open --json number,title,headRefName,author,createdAt
gh pr list -R envoy-ai/<repo> --base development --author app/github-actions --state open --json number,title,url
gh run list -R envoy-ai/<repo> --limit 10 --json databaseId,workflowName,headBranch,status,conclusion,createdAt
gh api repos/envoy-ai/<repo>/compare/staging...development --jq '{ahead: .ahead_by, commits: [.commits[].commit.message | split("\n")[0]]}'
gh api repos/envoy-ai/<repo>/compare/production...staging --jq '{ahead: .ahead_by}'
```

- Something already mid-deploy (an open `staging -> production` PR, a running build or deploy run): adopt that stage for that repo instead of starting over, and say so.
- A repo with no open release PR and nothing ahead of staging is skipped for the day. A repo with commits ahead of staging but no release PR means release-please found nothing releasable (chore/docs/ci only); it can still be promoted to staging but its production PR will fail the release gate, so report that and leave it out unless the user says otherwise.
- Show what ships: per repo the release PR title (the version) and the commit subjects ahead of staging.
- Propose the starting post (section 8, message 1). GO.

## 1. Cut the releases

- The release PR is the open PR on `development` by `app/github-actions` titled `chore(development): release X.Y.Z`. Exactly one per repo. `development` needs one approval; the bot authored it, so the user's own approval counts.
- Propose: approve (where not yet approved) and squash-merge them, naming each number and version. GO.

```
gh pr review <n> -R envoy-ai/<repo> --approve
gh pr merge <n> -R envoy-ai/<repo> --squash --match-head-commit <headRefOid>
```

- Wait for the `Release Please` workflow run on that push to succeed (`gh run list --workflow release-please.yml --branch development --limit 3`). It creates the tag and the GitHub Release. On backend and web, its `promote-to-staging` job then opens `release: vX.Y.Z to staging`, unless the 16:00 UTC weekday cron already opened `chore: promote development to staging`, in which case it leaves that one alone. The plugin has no promote job; it relies on the cron.

## 2. Promote to staging

- Find the open `development -> staging` PR (either title). It is always opened by automation. If none exists, propose triggering the action and wait for the PR it opens; never `gh pr create` this one. GO.

```
gh workflow run staging-promotion.yml -R envoy-ai/<repo>
```

- Wait for the PR's required checks (backend: `Integration`; web and plugin: their own). Zero approvals are needed on staging.
- Propose: merge with a merge commit. GO.

```
gh pr merge <n> -R envoy-ai/<repo> --merge --match-head-commit <headRefOid>
```

- Record the new staging head: `gh api repos/envoy-ai/<repo>/branches/staging --jq .commit.sha`.

## 3. Open the production PRs

Right after step 2, while staging builds.

- If an open `staging -> production` PR already exists, reuse it (a stale duplicate once sat open on web for two weeks). Otherwise propose opening one. GO.

```
gh pr create -R envoy-ai/<repo> --base production --head staging \
  --title "release: vX.Y.Z to production" \
  --body "$(printf 'Promotes v%s to production.\n\nChangelog: https://github.com/envoy-ai/%s/releases/tag/v%s\n\n%s' "$V" "$repo" "$V" "$titles")"
```

`$titles` is the list of PR titles in `production...staging` (`gh api repos/envoy-ai/<repo>/compare/production...staging --jq '.commits[].commit.message | split("\n")[0]'`, merged promotion commits filtered out).

- Propose the stamps post (section 8, message 2), tagging whoever the user names. GO.
- The skill cannot approve these (it authored them as the user; GitHub rejects self-approval). It waits for a teammate's approval, `gate / Release cut` and `Integration` (plugin: approval only) and reports what is missing each tick.

## 4. Wait for staging to land

Read-only, per repo.

- Backend: the `build-and-publish` run on `staging` succeeded, then the `deploy` run it triggered succeeded (`gh run list --workflow deploy.yml --limit 5`; a run with conclusion `skipped` is the per-environment concurrency queue replacing a pending run, not a failure; wait for the one that ran). Then check the live version: `curl -fsS https://staging.tryenvoy.ai/api/version` must show `git_sha` equal to the staging head from step 2 and `schema_status` `match`.
- Web: the `deploy` run on `staging` succeeded.
- Plugin: the `branch-build` run on `staging` succeeded.
- Failure: that repo stops. Its production PR is never proposed for merge. Report the failing step from `gh run view <id> --log-failed`. On the backend the API step rolls itself back to blue on a health failure; a failure at a later step means the API is on the new code while worker, beat and LiveKit are on the old, and the report says exactly that. Do not retry the run without a GO, and retry infrastructure failures at most once.

## 5. Merge to production

One repo at a time, in this order: backend, then web, then plugin. The backend advertises the plugin's minimum version and the web's types are generated from it, so a client never lands on production ahead of its backend. Web is not proposed until the backend finished step 6; the plugin not until web did.

- Ready means: step 4 passed, the production PR has a teammate's approval, its checks are green, `mergeable` is `MERGEABLE` and `mergeStateStatus` is `CLEAN`. Until then, wait and say what is missing.
- Propose: merge `<repo>` production PR with a merge commit. GO, one per repo.

```
gh pr merge <n> -R envoy-ai/<repo> --merge --match-head-commit <headRefOid>
```

## 6. Wait for production to land

Same checks as step 4 against production.

- Backend: `build-and-publish` on `production`, then the `deploy` run; `curl -fsS https://tryenvoy.ai/api/version` with `git_sha` equal to the production head and `schema_status` `match`. The LiveKit Cloud step is `continue-on-error`; read the run and report if it failed, since the run still shows green.
- Web: `deploy` on `production`.
- Plugin: `branch-build` and `store-publish` on `production`; quote the store step summary (submitted, or skipped because the version did not change, or which pending submission it cancelled).
- Failure: report the step, name the previous version (the production manifest before this merge) and the exact restore: backend `gh workflow run rollback.yml -R envoy-ai/envoyai-backend -f environment=production -f version=<previous>` (a forward deploy of the `v<previous>` image, worker and beat pause 10 to 30 minutes, LiveKit Cloud is not rolled back), web `gh workflow run rollback.yml -R envoy-ai/envoyai-web -f environment=production` (Cloudflare's previous deployment). Proposing it is fine; running it needs its own GO. Neither rollback has ever been run in this org, say so in the proposal.

## 7. Report and close

- Per repo: version, production sha, the build and deploy run links, store status for the plugin.
- Propose the closing post (section 8, message 3). GO.
- Stop the loop.

## 8. Slack posts

Three posts in `#devs` on a normal day, a fourth only when a repo fails. Show the exact text in the proposal and, on GO, send it with the Slack send tool (the GO is the approval; do not draft it for the user to send). No Slack tool in the client: show the text and say it was not posted. Professional English, one-liners plus links, no persona.

1. Starting (step 0): `Starting deploy: backend v1.62.0, web v1.53.0, plugin v1.22.0. Staging first, prod once stamped.`
2. Stamps (step 3):
   ```
   Stamps please for today's prod PRs, staging is deploying now:
   • backend https://github.com/envoy-ai/envoyai-backend/pull/2541  @Saumil @Myles
   • web https://github.com/envoy-ai/envoyai-web/pull/706  @Kaushik
   • plugin https://github.com/envoy-ai/envoyai-chrome-plugin/pull/254  @Kaushik
   Changelog: https://github.com/envoy-ai/envoyai-backend/releases/tag/v1.62.0
   ```
3. Done (step 7): `Deployed: backend v1.62.0 (2963d8f, schema match), web v1.53.0, plugin v1.22.0 submitted to the Chrome Web Store, publishes on Google's approval.`
4. Failure (step 4 or 6, replaces the done line for that repo): `Backend prod deploy failed at "Deploy Worker": API is on v1.62.0, worker/beat/livekit still on v1.61.0. Not rolled back. Run: <url>`

No post for the release merges, the staging merges or the production merges themselves.

## Per tick

Short and plain:

```
backend  v1.62.0  stage 4: staging deploy running (started 14:19, ~17 min)
web      v1.53.0  stage 5: waiting on a stamp for #706
plugin   v1.22.0  stage 2: promotion PR #253 checks pending
did: <what this tick changed after a GO, or "nothing">
needs GO: <the exact proposed list, or "nothing">
next: <when the next tick is, or "done">
```

## Never

Approve a PR the user authored, or any PR without a GO. Merge with `--admin` or `--auto`. Squash into staging or production. `gh pr create` the development to staging PR. Open a second promotion PR for a pair that already has one. Run `az`. Run `rollback.yml`, rerun a failed run, or run any workflow without a GO. Edit, rerun or weaken a check, an eval, or a scenario to get green. Act on a GO that was given for a different list. Post to Slack without a GO, or leave a GO'd post as a draft.
