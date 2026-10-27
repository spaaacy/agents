# Slack PR review runner

This personal background tool watches `#devs` (`C0B2JHDCQ6M`) for new messages mentioning Aakif (`U0B11TF094H`) with links to pull requests in `envoy-ai`. It reuses the existing Codex Slack connection and GitHub login (`spaaacy`); it stores no access tokens.

It checks once per minute, including thread replies through Slack search. Slack search indexing can add delay. A one-day overlap catches delayed indexing; the last successful checkpoint also catches messages received while the Mac was asleep. Only requests posted after activation are eligible. Bots and Aakif's own messages are excluded. If one message assigns different PRs to different people, the adjacent mention group selects Aakif's links.

The collector calls Codex's documented `mcpServer/tool/call` protocol without a model turn. Model usage starts only when a new PR needs review. The worker uses the model and reasoning settings in the normal Codex configuration, currently GPT-6 Astra with extra-high reasoning. One review runs at a time while the collector continues checking Slack.

## Review behavior

Each review gets a separate checkout at the PR's exact commit. Existing local repositories supply Git objects when available; other repositories in `envoy-ai` are cloned using GitHub CLI. Main checkouts stay on their existing branches. The first Codex turn runs `$review`; a second turn resumes that exact session and runs the draft step of `$post-review`. It saves the full review, the draft, session ID, and execution logs under `state/reviews/<key>/`.

The worker requests read-only review behavior, uses normal Codex protections with automatic approval review, and never bypasses the sandbox. Source files must remain unchanged. It does not run deployments or Azure CLI commands. Tool permission or sign-in failures are reported as failures, never answered with a fabricated approval.

Publishing remains disabled until Aakif explicitly authorizes it. In post mode the separate publisher sends one GitHub `COMMENT` review containing the summary and inline findings, under `spaaacy`. It never approves, requests changes, merges, pushes code, or messages Slack. It binds the review to the reviewed commit and rechecks both the PR head and base immediately before posting. Closed or changed PRs are marked superseded instead of receiving an old draft. A fresh mention at a new head starts a new review.

The queue records each message and PR, and each PR commit is reviewed once. A hidden marker in posted reviews prevents duplicates. An uncertain posting result is never blindly retried. Invalid inline anchors are placed in the summary instead. Interrupted or failed reviews require an explicit retry; `status` shows the error and artifacts. No result is silently counted as success.

## Live terminal view

At login, `ai.envoy.slack-pr-review-viewer` opens Terminal with a live view of the worker. It shows Slack scan health, queued requests, Codex progress messages and commands, findings, and posted-review links. It reads the existing state and event files; opening it never starts another review or posts anything. Raw tool output remains in the saved event logs.

Closing the window or pressing Ctrl-C closes only the view. The background worker continues. To reopen it, double-click `Watch Reviews.command`, or run:

```sh
open -a Terminal ~/.agents/slack-pr-review/Watch\ Reviews.command
```

The viewer code is `watch.py`. Its separate login entry is `~/Library/LaunchAgents/ai.envoy.slack-pr-review-viewer.plist`; it runs once at login and does not reopen the window after you close it. The worker's own login entry and posting permissions are unchanged. A saved conversation can also be resumed with `codex resume <session-id>`; the viewer prints that command for completed runs. This is a live output view, not an interactive Codex conversation.

## Worker commands

```sh
cd ~/.agents/slack-pr-review
python3 runner.py status
python3 runner.py preview --hours 24
python3 runner.py draft https://github.com/envoy-ai/envoyai-backend/pull/2909
python3 runner.py disable
python3 runner.py retry <job-key>
```

`preview` only shows matching links. `draft` runs both skills and never posts, even when post mode is enabled; run it only while the background worker is stopped. `retry` preserves the failed artifacts and queues a new attempt; it also requires the worker to be stopped. An uncertain post needs inspection on GitHub before any manual retry.

After explicit authorization, choose one activation command:

```sh
python3 runner.py enable --mode draft
# Or, only with Aakif's standing authorization for this workflow:
python3 runner.py enable --mode post --authorize-comment-posting
```

Activation starts from the current time; it never bulk-reviews old requests. Changing mode does not automatically publish previously prepared drafts. Disable stops new work and removes posting authorization; an already-sent HTTP request cannot be recalled.

The LaunchAgent is `ai.envoy.slack-pr-review`, installed at `~/Library/LaunchAgents/ai.envoy.slack-pr-review.plist`. It starts at login and restarts after a crash. The Mac must be awake and logged in; it does not prevent sleep. State, draft files, and logs are private to the local user. No automatic deletion of review artifacts is configured.

```sh
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.envoy.slack-pr-review.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.envoy.slack-pr-review.plist
tail -f ~/.agents/slack-pr-review/state/runner.log
```

## Validation and protocol references

Run `python3 -m unittest -v test_runner.py`. Tests cover mention-to-link assignment, pagination, delayed-index overlaps, startup history boundaries, duplicate requests, malformed connector responses, changed commits, comment anchors, disabled posting, and ambiguous write outcomes. A live `preview` validates the installed Slack connection. A real PR dry run validates both skills and session continuity without publishing.

Codex references: [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode), [app-server protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md). Slack access is through the connected plugin; no custom Slack app or copied OAuth token is needed.

Known limits: this relies on the installed Codex app-server protocol and the Slack connector's formatted search results; a format change fails the scan without advancing its checkpoint. Mentions split across separate Slack messages, requests without a literal GitHub PR URL, and edits to messages older than the search overlap are not new triggers. Results are available in the saved Codex session and local artifacts; this version does not send Slack notifications.
