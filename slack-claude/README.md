# Slack Claude

Ask Claude Code on this Mac something from Slack, and it reads the conversation, answers, and replies in the thread. A follow-up in the same thread continues the same Claude session.

- In a channel or private channel it was invited to (`/invite @claude-aakif`): mention `@claude-aakif`. It answers as the bot.
- In its own DM: write anything, no mention needed.
- In a DM or group DM with teammates, where Slack keeps apps out: start a message with `@claude`. The answer is posted in that conversation under your name, starting with `🤖 claude:`, so everyone in it sees it. This needs your user token (Setup, step 7).

It answers only Aakif (`U0B11TF094H`). Anyone else's mention or `@claude` is ignored, so nobody else can steer an agent that runs with this Mac's GitHub login, checkouts and database connection.

## What it can and cannot do

It is read-only. It can read the four checkouts and their git history, GitHub pull requests and runs, Linear, Datadog, and the read-only production database (`pg-readonly`). It cannot edit files, commit, push, open or comment on pull requests, change tickets, or post anywhere. When a request needs one of those, it says what should be done.

How that is enforced, not just asked for:

- Claude starts with none of your saved Claude settings (`--setting-sources ""`), because those allow `git push`, `git commit`, `gh pr` and Linear edits.
- `--permission-mode dontAsk` refuses every tool not on `allowed_tools` in `config.json`. A chained command is refused when any part of it is.
- `claude-settings.json` denies the write tools outright: file edits, push, commit, reset, checkout, stash (also in their `git -C` form), `gh api`, PR writes, `az`, and the Slack, Claude Docs and agent-bus connectors.
- The bot posts the answer itself; the model never holds a Slack write tool. Your user token is used only to read the DM a request came from and post the marked answer in it, and only when its owner is you.
- Other people's messages go to Claude as context, labelled as not instructions.

Answers are posted where you asked, so everyone in that conversation can read what it found. Each answer costs about $0.20 to $0.50.

## Live view

A Terminal window opens at login and shows the bot as it works: whether it is connected, each request, what Claude reads, searches and runs, refused tools, and a link to the answer with the command to open the session on the laptop. Closing the window only closes the view; the bot keeps running. To reopen it, double-click `Watch Slack Claude.command`, or:

```sh
open -a Terminal ~/.agents/slack-claude/Watch\ Slack\ Claude.command
```

It reads `state/status.json` (a heartbeat the bot writes every 10 seconds) and `state/requests/<id>/` (`request.json` with each request's state, `events.jsonl` with Claude's events as they stream). Thinking and raw tool output are not shown; they are in `events.jsonl`. Its login item is `~/Library/LaunchAgents/ai.envoy.slack-claude-viewer.plist`, which opens the window once per login.

## Setup

Done once. Everything here stays on this Mac except the Slack app itself.

1. Create the Slack app: open https://api.slack.com/apps, choose Create New App, then From a manifest, pick the workspace, and paste `slack-app-manifest.json` (`pbcopy < slack-app-manifest.json`). The workspace may need an admin to approve it.
2. App-level token: on Basic Information, under App-Level Tokens, generate one with the `connections:write` scope. Store it (the command asks for the value without echoing it):
   ```sh
   security add-generic-password -a "$USER" -s slack-claude-app-token -w
   ```
3. Bot token: under Install App, install it to the workspace, copy the Bot User OAuth Token (`xoxb-...`), and store it:
   ```sh
   security add-generic-password -a "$USER" -s slack-claude-bot-token -w
   ```
4. Check: `.venv/bin/python bot.py check` confirms the tokens, the Slack sign-in, the claude CLI and the MCP servers. It posts nothing.
5. Start it, now and at every login:
   ```sh
   cp ai.envoy.slack-claude.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.envoy.slack-claude.plist
   cp ai.envoy.slack-claude-viewer.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.envoy.slack-claude-viewer.plist
   ```
6. Invite it to each channel you want to use it in: `/invite @claude-aakif`.
7. Answers in DMs with teammates. An app created from an earlier manifest needs the current one first: on the app's App Manifest page, paste `slack-app-manifest.json` over it and save, then on Install App click Reinstall to Workspace. Copy the User OAuth Token (`xoxp-...`) from that page, store it, and restart the bot:
   ```sh
   security add-generic-password -a "$USER" -s slack-claude-user-token -w
   launchctl kickstart -k gui/$(id -u)/ai.envoy.slack-claude
   ```
   `bot.py check` then reports the user token as yours, and the live view says "answers in other DMs on".

The Python environment is `.venv` (Python 3.12, `slack_sdk` only). Recreate it with `/Users/aakifmohamed/.pyenv/versions/3.12.8/bin/python3 -m venv .venv && .venv/bin/pip install slack_sdk`. Replace a stored token by adding `-U` to its `security add-generic-password` command.

## Commands

```sh
cd ~/.agents/slack-claude
.venv/bin/python bot.py check                          # tokens, Slack sign-in, claude CLI, MCP servers
.venv/bin/python bot.py ask "what changed in #3053?"   # one question with the bot's limits, printed here
.venv/bin/python watch.py --once                       # the bot's status, once
.venv/bin/python -m unittest -v test_bot.py            # tests
tail -f state/bot.log
launchctl kickstart -k gui/$(id -u)/ai.envoy.slack-claude                              # restart, e.g. after a code change
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.envoy.slack-claude.plist     # stop
```

## Known limits

- The Mac must be awake and online. Slack retries a missed event a few times, then drops it; a request made while the laptop is closed is not answered later.
- In a DM or group DM, the trigger is `@claude` at the very start of the message. A real `@claude-aakif` mention in a group DM the bot was added to is answered by the bot instead.
- Requests are answered one at a time, in order.
- `pg-readonly` reads production through its tunnel; when that tunnel is down, database questions fail.
- Request folders under `state/requests/` are never deleted automatically.
