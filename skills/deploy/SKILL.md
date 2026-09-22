---
name: deploy
description: Run the daily on-call deploy across envoyai-backend, envoyai-web and envoyai-chrome-plugin - cut the release-please releases, promote development to staging, open the staging to production PRs, wait for every build and deploy run, merge to production, verify. Two GOs from the user on a normal day (ship to staging, ship to production), a third only when something fails; it reads on its own but changes nothing outside a GO. Use when the user says deploy, promote, release, or ship to staging or production.
---

Deploy $ARGUMENTS: nothing means all three repos; `--only backend|web|plugin` limits it to one. Repos are `envoy-ai/envoyai-backend`, `envoy-ai/envoyai-web`, `envoy-ai/envoyai-chrome-plugin`, always addressed with `-R`; this skill needs no local checkout and never touches a working tree.

**Rule zero: nothing changes outside a GO.** On its own this skill only reads: `gh pr view/list`, `gh run list/view`, `gh api` reads, `curl` of the two version endpoints. Changes happen in two batches, each proposed as an exact list (PR numbers, head shas, the Slack text) and executed only after the user replies GO to that list:

- **GO 1, ship to staging:** the starting post, approving and squash-merging the release PRs, triggering the promotion action where no promotion PR exists, merging the promotion PRs once their checks pass, opening the production PRs, the stamps post. Then it waits.
- **GO 2, ship to production:** merging the production PRs in order (backend, then web, then plugin, each after the previous one has landed) and the done post. Asked once every repo's staging deploy has landed and every production PR is stamped and green.
- **GO 3, only on a failure:** re-running a run, `rollback.yml`, the failure post. Each proposed on its own.

A GO covers the PRs and shas it named. If something moves underneath it (a new commit on staging after GO 2, a PR closed and reopened, a check that went red after being green), the skill stops for that repo and asks again rather than merging something the user did not look at. Never `az`, in any form. Never merge with `--admin`.

**Approvals.** An approval through this skill is the user's own GitHub account stamping, so it is part of GO 1. The skill may approve a PR the user did not author (the release-please PRs, the automation-opened promotion PRs). It cannot approve the production PRs it opens: GitHub rejects self-approval, and the production ruleset wants one approval from someone other than the merger, so those wait for a teammate.

Under Claude Code, run it as `/loop /deploy` so the session re-checks on its own. Each tick re-reads GitHub, works out the stage each repo is in, then either does the next thing the current GO already covers, or proposes the next GO, or has nothing to change and says what it is waiting on and when it checks again. A tick with nothing new is a one-liner. In a client without a loop, one tick is the whole run: report and tell the user to run it again.

## The pipeline, per repo

- `development` -> squash-merge the release-please PR -> tag + GitHub Release -> a `development -> staging` PR is opened by automation -> merge commit -> staging builds and deploys -> `staging -> production` PR -> one human approval + release gate -> merge commit -> production builds and deploys.
- Backend: push to `staging`/`production` runs `build-and-publish.yml` (api, worker, livekit images), then `deploy.yml` via `workflow_run` (API blue-green with a health gate and auto-rollback to blue, schema self-check, worker, beat, LiveKit ACA, LiveKit Cloud). 15 to 18 minutes.
- Web: push runs `deploy.yml` (Cloudflare Worker). Under 3 minutes.
- Plugin: push runs `branch-build.yml` (zips + rolling `latest-<env>` pre-release); push to `production` also runs `store-publish.yml` (Chrome Web Store upload + submit, only when `package.json` version changed; cancels any submission still in review).
- Merge methods are fixed by rulesets: `development` accepts squash; `staging` and `production` accept merge commits only. Production needs one approval from someone other than the merger; backend and web production also require `gate / Release cut` (manifest version on staging differs from production) and `Integration`. The plugin's production checks ruleset is disabled: approval only.

## 0. Preflight (read-only)

```
gh auth status
gh pr list -R envoy-ai/<repo> --base production --state open --json number,title,headRefName,createdAt
gh pr list -R envoy-ai/<repo> --base staging --state open --json number,title,headRefName,author,createdAt
gh pr list -R envoy-ai/<repo> --base development --author app/github-actions --state open --json number,title,url,headRefOid
gh run list -R envoy-ai/<repo> --limit 10 --json databaseId,workflowName,headBranch,status,conclusion,createdAt
gh api repos/envoy-ai/<repo>/compare/staging...development --jq '{ahead: .ahead_by, commits: [.commits[].commit.message | split("\n")[0]]}'
gh api repos/envoy-ai/<repo>/compare/production...staging --jq '{ahead: .ahead_by}'
```

- Something already mid-deploy (an open `staging -> production` PR, a running build or deploy run): adopt that stage for that repo instead of starting over, and say so. If the adopted state is past GO 1's work, skip straight to waiting for GO 2.
- A repo with no open release PR and nothing ahead of staging is skipped for the day. A repo with commits ahead of staging but no release PR means release-please found nothing releasable (chore/docs/ci only); it can still be promoted to staging but its production PR will fail the release gate, so say that and leave it out unless the user includes it.
- Show what ships: per repo the release PR title (the version) and the commit subjects ahead of staging.
- PRs into `development` that look due to merge, per repo: `gh pr list -R envoy-ai/<repo> --base development --state open --json number,title,author,reviewDecision,isDraft,mergeable,statusCheckRollup,updatedAt,url`. Keep every non-draft PR that is approved, whatever its checks say (a red check with an approval usually means the author is fixing CI to ship today), plus any non-draft PR updated in the last 24 hours with green checks and no changes requested. Skip the release-please PR itself. List each with author, review state and check state (`approved, Eval sample red`); they become the nudge in GO 1 so nobody's near-done work misses the train by an hour.

## 1. Propose GO 1: ship to staging

One proposal, in this shape:

```
GO 1 would, in order:
  post #devs nudge for PRs due to merge: backend #2389 (@Saumil, approved, Eval sample red), web #702 (@Kaushik, approved, green); hold until each is merged, declared out, or 20 min pass with no answer
  post #devs: "Starting deploy: backend v1.62.0, web v1.53.0, plugin v1.22.0. Staging first, prod once stamped."
  approve + squash-merge release PRs: backend #2535 (a1b2c3d), web #705 (e4f5a6b), plugin #253 (c7d8e9f)
  merge dev->staging PRs once checks pass (backend #2537 exists; web and plugin: trigger staging-promotion.yml and wait for the PR)
  open staging->production PRs titled "release: vX.Y.Z to production"
  post #devs stamps request (@here)
Reply GO to run all of it, or name what to drop.
```

No PRs due to merge: the nudge line is omitted and there is no wait. The release PRs are named by number, not sha: a PR merged during the nudge window makes release-please rewrite its PR (new version, new changelog), so the head sha is read at merge time and reported then.

The stamps post always opens with `@here`; the skill never asks who to tag and never tags individuals unless the user names them in the GO reply. Then stop and wait.

## 2. Run GO 1

Each item runs on a tick when its precondition is met; ticks in between report progress.

- Nudge (section 6, message 0), one post listing every PR due to merge with its author tagged. Resolve each GitHub login to a Slack user by the name on their GitHub profile (`gh api users/<login> --jq .name`, then Slack user search); an unresolved login is written as plain text. Then hold the release merges per PR: merged or closed, or the author says it is out, releases the hold; the author saying it is in ("yes", "merging", "give me 10") keeps the hold until it merges, up to 60 minutes, after which the skill reports and asks the user whether to keep waiting; no answer at all releases the hold after 20 minutes. Read the thread replies each tick (Slack thread read tool). A PR merged in the window changes what ships: re-read the release PR (new version, new head) and say so in the next tick.
- Release PRs (`chore(development): release X.Y.Z` by `app/github-actions`, one per repo; `development` needs one approval and the bot authored it):

```
gh pr review <n> -R envoy-ai/<repo> --approve
gh pr merge <n> -R envoy-ai/<repo> --squash --match-head-commit <headRefOid>
```

- Wait for the `Release Please` run on that push to succeed (`gh run list --workflow release-please.yml --branch development --limit 3`): tag, GitHub Release, and on backend and web a `release: vX.Y.Z to staging` PR from its `promote-to-staging` job, unless the 16:00 UTC weekday cron already opened `chore: promote development to staging`. The plugin has no promote job.
- Promotion PR: find the open `development -> staging` PR (either title). None open: `gh workflow run staging-promotion.yml -R envoy-ai/<repo>` and wait for the PR it opens. Never `gh pr create` this one.
- Wait for the PR's required checks (backend: `Integration`; web and plugin: their own). No approval is needed on staging. Then:

```
gh pr merge <n> -R envoy-ai/<repo> --merge --match-head-commit <headRefOid>
```

- Record the staging head: `gh api repos/envoy-ai/<repo>/branches/staging --jq .commit.sha`.
- Production PR: reuse an open `staging -> production` PR if one exists (a stale duplicate once sat open on web for two weeks). Otherwise:

```
gh pr create -R envoy-ai/<repo> --base production --head staging \
  --title "release: vX.Y.Z to production" \
  --body "$(printf 'Promotes v%s to production.\n\nChangelog: https://github.com/envoy-ai/%s/releases/tag/v%s\n\n%s' "$V" "$repo" "$V" "$titles")"
```

`$titles` is the list of PR titles in `production...staging` (`gh api repos/envoy-ai/<repo>/compare/production...staging --jq '.commits[].commit.message | split("\n")[0]'`, merged promotion commits filtered out).

- Stamps post (section 6, message 2) as soon as all production PRs exist.

## Late add

If, after GO 1 ran, the user or a nudge reply names a PR that should have gone in ("2389 should have been in this"), that repo goes back to GO 1 without a new GO: hold its production PR, wait for the PR to merge into `development`, then re-run the release step (release-please opens a fresh `chore(development): release` PR with the next version; approve + squash-merge it), wait for the promotion PR (the promote job or `staging-promotion.yml`), merge it, and re-check the existing production PR: it is branch-to-branch so it picks up the new staging head on its own, but its approval and `gate / Release cut` re-evaluate and the stamps post is repeated for that repo. The other repos keep going. Say in the tick what version the repo is now shipping.

## 3. Wait for staging and stamps (read-only)

Per repo, staging has landed when:

- Backend: the `build-and-publish` run on `staging` succeeded, then the `deploy` run it triggered succeeded (`gh run list --workflow deploy.yml --limit 5`; a run with conclusion `skipped` is the per-environment concurrency queue replacing a pending run, not a failure; wait for the one that ran). Then `curl -fsS https://staging.tryenvoy.ai/api/version` shows `git_sha` equal to the staging head and `schema_status` `match`.
- Web: the `deploy` run on `staging` succeeded.
- Plugin: the `branch-build` run on `staging` succeeded.

And its production PR is ready when a teammate approved it, its checks are green, `mergeable` is `MERGEABLE` and `mergeStateStatus` is `CLEAN`. Until then, wait and say what is missing.

Failure here: that repo stops and is dropped from GO 2. Report the failing step from `gh run view <id> --log-failed`. On the backend the API step rolls itself back to blue on a health failure; a failure at a later step means the API is on the new code while worker, beat and LiveKit are on the old, and the report says exactly that. Re-running is GO 3; infrastructure failures are retried at most once.

## 4. Propose and run GO 2: ship to production

Proposed once every non-dropped repo is landed and stamped, in this shape:

```
GO 2 would, in order:
  merge backend #2541 (staging 2963d8f, approved by Saumil) -> wait for build-and-publish + deploy + /api/version
  merge web #706 (staging 7a8b9c0, approved by Kaushik) -> wait for deploy
  merge plugin #254 (staging 1d2e3f4, approved by Kaushik) -> wait for branch-build + store-publish
  post #devs: "Deployed: backend v1.62.0 (…), web v1.53.0, plugin v1.22.0 …"
Reply GO, or name a repo to hold back.
```

Order is fixed: backend, then web, then plugin. The backend advertises the plugin's minimum version and the web's types are generated from it, so a client never lands on production ahead of its backend. Each merge waits for the previous repo's production deploy to land.

```
gh pr merge <n> -R envoy-ai/<repo> --merge --match-head-commit <headRefOid>
```

Before each merge, re-check the PR: head sha still the one GO 2 named, approval still present, checks still green. Anything different: hold that repo and ask.

Production has landed when:

- Backend: `build-and-publish` on `production`, then the `deploy` run; `curl -fsS https://tryenvoy.ai/api/version` with `git_sha` equal to the production head and `schema_status` `match`. The LiveKit Cloud step is `continue-on-error`; read the run and report if it failed, since the run still shows green.
- Web: `deploy` on `production`.
- Plugin: `branch-build` and `store-publish` on `production`; quote the store step summary (submitted, or skipped because the version did not change, or which pending submission it cancelled).

Failure here: stop the order (web is not merged if the backend deploy failed). Report the step, name the previous version (the production manifest before this merge) and the exact restore: backend `gh workflow run rollback.yml -R envoy-ai/envoyai-backend -f environment=production -f version=<previous>` (a forward deploy of the `v<previous>` image, worker and beat pause 10 to 30 minutes, LiveKit Cloud is not rolled back), web `gh workflow run rollback.yml -R envoy-ai/envoyai-web -f environment=production` (Cloudflare's previous deployment). Running it is GO 3. Neither rollback has ever been run in this org; say so in the proposal. The failure post (section 6, message 4) is part of the same GO 3 proposal.

## 5. Report and close

- Per repo: version, production sha, the build and deploy run links, store status for the plugin.
- Send the done post (already covered by GO 2).
- Stop the loop.

## 6. Slack posts

Three posts in `#devs` on a normal day, a nudge before them when approved PRs are sitting unmerged, a failure post only when a repo fails. The exact text is in the GO proposal; on GO, send it with the Slack send tool (never leave it as a draft for the user to send). No Slack tool in the client: show the text and say it was not posted. Professional English, one-liners plus links, no persona.

0. Nudge (GO 1, only when preflight found PRs due to merge): `Deploy starts in ~20 min. These look close, are they going in first? Reply in or out. @Saumil https://github.com/envoy-ai/envoyai-backend/pull/2389 (approved, Eval sample red) · @Kaushik https://github.com/envoy-ai/envoyai-web/pull/702 (approved, green)`
1. Starting (GO 1): `Starting deploy: backend v1.62.0, web v1.53.0, plugin v1.22.0. Staging first, prod once stamped.`
2. Stamps (GO 1, once the production PRs exist):
   ```
   @here stamps please for today's prod PRs, staging is deploying now:
   • backend https://github.com/envoy-ai/envoyai-backend/pull/2541
   • web https://github.com/envoy-ai/envoyai-web/pull/706
   • plugin https://github.com/envoy-ai/envoyai-chrome-plugin/pull/254
   Changelog: https://github.com/envoy-ai/envoyai-backend/releases/tag/v1.62.0
   ```
3. Done (GO 2): `Deployed: backend v1.62.0 (2963d8f, schema match), web v1.53.0, plugin v1.22.0 submitted to the Chrome Web Store, publishes on Google's approval.`
4. Failure (GO 3, replaces the done line for that repo): `Backend prod deploy failed at "Deploy Worker": API is on v1.62.0, worker/beat/livekit still on v1.61.0. Not rolled back. Run: <url>`

No post for the release merges, the staging merges or the production merges themselves.

## Per tick

Short and plain:

```
backend  v1.62.0  staging deploy running (started 14:19, ~17 min); prod #2541 not yet stamped
web      v1.53.0  staging landed; prod #706 stamped by Kaushik, ready
plugin   v1.22.0  promotion PR #253 checks pending
did: <what this tick did under the current GO, or "nothing">
needs GO: <GO 2 proposal / GO 3 proposal / "nothing, waiting on <x>">
next: <when the next tick is, or "done">
```

Tick cadence: 3 to 5 minutes while a run is in flight, 15 to 20 minutes while waiting on a stamp or a GO.

## Never

Approve a PR the user authored. Merge with `--admin` or `--auto`. Squash into staging or production. `gh pr create` the development to staging PR. Open a second promotion PR for a pair that already has one. Run `az`. Run `rollback.yml`, rerun a failed run, or run any workflow outside a GO. Edit, rerun or weaken a check, an eval, or a scenario to get green. Merge a PR whose head sha, approval or checks changed since the GO that named it. Post to Slack outside a GO, or leave a GO'd post as a draft.
