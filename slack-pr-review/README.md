# Slack PR review runner

This personal background tool watches `#devs` (`C0B2JHDCQ6M`) for new messages mentioning Aakif (`U0B11TF094H`) with links to pull requests in `envoy-ai`. It reuses the existing Codex Slack connection and GitHub login (`spaaacy`); it stores no access tokens.

It checks once per minute, including thread replies through Slack search. Slack search indexing can add delay. A one-day overlap catches delayed indexing; the last successful checkpoint also catches messages received while the Mac was asleep. Only requests posted after activation are eligible. Bots and Aakif's own messages are excluded. If one message assigns different PRs to different people, the adjacent mention group selects Aakif's links.

The collector calls Codex's documented `mcpServer/tool/call` protocol without a model turn. Model usage starts only when a new PR needs review. One review runs at a time while the collector continues checking Slack.

## Who reviews: Codex or Claude

`agents-engine codex` or `agents-engine claude` (see `~/.agents/README.md`) picks the agent for every background tool, this one included; `agents-engine` prints the current choice, and `python3 runner.py reviewer [codex|claude]` does the same. No restart is needed: the worker reads it before each review, and a review already running finishes with its own reviewer. Each job records its reviewer, and `watch-pr-reviews` shows the current one. The Slack collector stays on Codex either way.

- Codex runs with the model and effort the switch names (gpt-6.1-sol at medium by default), with its normal protections and automatic approval review.
- Claude runs headless, the slack-claude bot's way: none of the saved Claude settings (`--setting-sources ""`), `--permission-mode dontAsk` with `claude_allowed_tools` in `config.json` (reads, git and gh reads, tests), and the deny list in `claude-settings.json` (no edits, commits, pushes, `gh api`, PR comments or reviews, `az`, Slack). The review and post-review skills are given to it inline, and the draft comes back as structured output (`--json-schema`). Reopen a Claude review with `cd <review folder>/checkout && claude --resume <session>`; the viewer prints it.

Both reviewers' drafts go through the same checks and the same publisher.

## Review behavior

Each review gets a separate checkout at the PR's exact commit. Existing local repositories supply Git objects when available; other repositories in `envoy-ai` are cloned using GitHub CLI. Main checkouts stay on their existing branches. The first turn runs the review skill; a second turn resumes that exact session and runs the draft step of the post-review skill. It saves the full review, the draft, session ID, and execution logs under `state/reviews/<key>/`.

The worker requests read-only review behavior, uses normal Codex protections with automatic approval review, and never bypasses the sandbox. Source files must remain unchanged. It does not run deployments or Azure CLI commands. Tool permission or sign-in failures are reported as failures, never answered with a fabricated approval.

Publishing remains disabled until Aakif explicitly authorizes it. In post mode the separate publisher sends one GitHub `COMMENT` review containing the summary and inline findings, under `spaaacy`. It never approves, requests changes, merges, pushes code, or messages Slack. It binds the review to the reviewed commit and rechecks both the PR head and base immediately before posting. Closed or changed PRs are marked superseded instead of receiving an old draft. A fresh mention at a new head starts a new review.

The queue records each message and PR, and each PR commit is reviewed once. A hidden marker in posted reviews prevents duplicates. An uncertain posting result is never blindly retried. Invalid inline anchors are placed in the summary instead. Interrupted or failed reviews require an explicit retry; `status` shows the error and artifacts. No result is silently counted as success.

## Live terminal view

Run `watch-pr-reviews` in any terminal for a live view of the worker: Slack scan health, queued requests, Codex progress messages and commands, findings, and posted-review links. It reads the existing state and event files; running it never starts another review or posts anything. Raw tool output remains in the saved event logs. Nothing opens on its own at login; the worker itself still starts at login.

Ctrl-C closes only the view; the background worker continues. `watch-pr-reviews --once` prints the current status and exits. The viewer code is `watch.py`; the command lives in `~/.agents/bin/` and `install.sh` links it into `~/.local/bin`. A saved conversation can also be resumed with `codex resume <session-id>`; the viewer prints that command for completed runs. This is a live output view, not an interactive Codex conversation.

## Worker commands

```sh
cd ~/.agents/slack-pr-review
python3 runner.py status
python3 runner.py reviewer          # the engine every background tool runs on
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
