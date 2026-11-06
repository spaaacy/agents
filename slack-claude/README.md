# Slack Claude

Mention `@claude-aakif` in a Slack thread, or send it a direct message, and Claude Code on this Mac reads the conversation, answers your request, and replies in the thread. A follow-up in the same thread continues the same Claude session.

It answers only Aakif (`U0B11TF094H`). Anyone else's mention is ignored, so nobody else can steer an agent that runs with this Mac's GitHub login, checkouts and database connection.

## What it can and cannot do

It is read-only. It can read the four checkouts and their git history, GitHub pull requests and runs, Linear, Datadog, and the read-only production database (`pg-readonly`). It cannot edit files, commit, push, open or comment on pull requests, change tickets, or post anywhere. When a request needs one of those, it says what should be done.

How that is enforced, not just asked for:

- Claude starts with none of your saved Claude settings (`--setting-sources ""`), because those allow `git push`, `git commit`, `gh pr` and Linear edits.
- `--permission-mode dontAsk` refuses every tool not on `allowed_tools` in `config.json`. A chained command is refused when any part of it is.
- `claude-settings.json` denies the write tools outright: file edits, push, commit, reset, checkout, stash (also in their `git -C` form), `gh api`, PR writes, `az`, and the Slack, Claude Docs and agent-bus connectors.
- The bot posts the answer itself; the model never holds a Slack write tool.
- Other people's messages in the thread go to Claude as context, labelled as not instructions.

Answers are posted where you asked, so in a public channel anyone there can read what it found. Each answer costs about $0.20 to $0.50.

## Setup

Done once. Everything here stays on this Mac except the Slack app itself.

1. Create the Slack app: open https://api.slack.com/apps, choose Create New App, then From a manifest, pick the workspace, and paste `slack-app-manifest.json`. The workspace may need an admin to approve it.
2. App-level token: on the app's Basic Information page, under App-Level Tokens, generate one with the `connections:write` scope. Store it (the command asks for the value without echoing it):
   ```sh
   security add-generic-password -a "$USER" -s slack-claude-app-token -w
   ```
3. Bot token: under Install App, install it to the workspace, copy the Bot User OAuth Token (`xoxb-...`), and store it:
   ```sh
   security add-generic-password -a "$USER" -s slack-claude-bot-token -w
   ```
4. Check: `.venv/bin/python bot.py check` confirms both tokens, the Slack sign-in, the claude CLI and the MCP servers. It posts nothing.
5. Start it, now and at every login:
   ```sh
   cp ai.envoy.slack-claude.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.envoy.slack-claude.plist
   ```
6. Invite it to each channel you want to use it in: `/invite @claude-aakif`.

The Python environment is `.venv` (Python 3.12, `slack_sdk` only). Recreate it with `python3 -m venv .venv && .venv/bin/pip install slack_sdk`.

## Using it

- In a thread: `@claude-aakif why did the outbox stall at 10:02?` It reacts 👀 while working, replies in the thread, then ✅. A failure is said in the thread with ❌.
- A top-level mention in a channel also sees the 15 messages before it.
- In its DM: write anything, no mention needed.
- Follow up in the same thread to continue the conversation. Each thread is one Claude session (`state/sessions.json`); `claude --resume <session-id>` opens it on the laptop.

## Commands

```sh
cd ~/.agents/slack-claude
.venv/bin/python bot.py check                   # tokens, Slack sign-in, claude CLI, MCP servers
.venv/bin/python bot.py ask "what changed in #3053?"   # one question with the bot's limits, printed here
.venv/bin/python -m unittest -v test_bot.py    # tests
tail -f state/bot.log
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.envoy.slack-claude.plist   # stop
```

## Known limits

- The Mac must be awake and online. Slack retries a missed event a few times, then drops it; a mention made while the laptop is closed is not answered later.
- Slack does not allow apps in a one-to-one DM between two people. Use a channel, a group DM that includes the bot, or the bot's own DM.
- Requests are answered one at a time, in order.
- `pg-readonly` reads production through its tunnel; when that tunnel is down, database questions fail.
