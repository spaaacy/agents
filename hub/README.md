# Agents hub

One background process on this Mac runs every unattended agent: it answers Aakif in Slack (`slack_ask`), investigates Datadog pages while he is on call (`on_call`), and reviews the PRs he is tagged on in #devs (`pr_review`). It has one job queue, one Slack connection, one switch between Codex and Claude, a local dashboard, and control commands in Slack. It replaces the three separate services (slack-claude, on-call-runner, slack-pr-review) that were built one at a time.

## How it fits together

- Plugins notice work and do it. Each one is a module under `plugins/` with a `Plugin` subclass. Its sources (a Slack event, a poll of Datadog, a search of #devs) run on their own threads and only add jobs to the queue. Its `handle(job, ctx)` does one job, normally one agent turn through `ctx.run_agent(...)`, and returns a `JobResult`.
- The scheduler (`core/scheduler.py`) runs queued jobs on a few worker threads (`scheduler.slots` in `config.json`, 2 by default), highest priority first, each plugin under its own limit (`max_concurrent`). An on-call page goes ahead of a PR review.
- The job store (`core/store.py`) is one SQLite file. Every job keeps its state (queued, running, done, failed, cancelled), the engine and session that ran it, the link to what it produced, its token count or cost, and its error. A job left running when the hub stopped is marked failed, never counted as done; retry it.
- The agent runner (`core/agent.py`) runs one turn on Codex or Claude, whichever the switch names, with the same limits for every plugin. Sessions are stored with their engine, so a thread one agent started is never handed to the other.
- Slack (`core/slack.py`) is one Socket Mode connection shared by all plugins. The bot's name is read from Slack at start, so renaming the app needs no code change. Tokens stay in the Keychain under the names in `config.json` (`slack.keychain`).

## Adding an agent

1. Create `plugins/<name>/plugin.py` with a subclass of `hub.core.plugin.Plugin`. Set `name` (must match the config key), `title`, `priority` (higher runs first) and `max_concurrent`.
2. Implement `start()` to launch its sources (use `self.spawn(loop, "name")` for a thread, or `self.hub.slack.subscribe(handler)` for Slack events) and `handle(job, ctx)` to do one job. Enqueue with `self.enqueue(title, payload, dedupe=...)`; the dedupe key keeps a redelivered event from becoming a second job.
3. Optional: `status()` (facts for the dashboard), `checks()` (lines for `agents check`), `commands()` (control commands, `!<name> <cmd>`), `cli()` (one-shot terminal commands, `agents <name> <cmd>`).
4. Put its settings in `plugins/<name>/config.json` (allow lists, timeouts, its own options). Keys under the plugin's entry in the hub's `config.json` override single values.
5. Add one line to `config.json`'s `plugins`: `"<name>": {"class": "hub.plugins.<name>.plugin:<Class>"}`. `"enabled": false` turns a plugin off without removing it.
6. Restart the hub. Each plugin gets its own key-value scope (`self.get`/`self.put`), its own folder under `~/.agents/state/hub/plugins/<name>/`, and a run folder per job.

## Codex or Claude

```sh
agents engine                                            # what agents run on now
agents engine codex                                      # Codex (gpt-6.1-sol, medium effort by default)
agents engine codex --model gpt-6.1-sol --effort medium  # change the Codex model or effort; kept for later switches
agents engine claude                                     # Claude
agents model                                             # every model of both engines with its effort levels
agents model opus                                        # pick a model by id or short name (sol, astra, opus...); switches the engine to match
agents effort xhigh                                      # the current model's effort; `default` goes back to the model's own
agents model reviews astra --effort xhigh                # PR reviews only: `reviews` first works with engine, model and effort
```

The choice is read before every job, so it applies to the next one with no restart; a job already running finishes on its engine. It is stored in `~/.agents/state/engine.json`. PR reviews have a setting of their own (`~/.agents/state/engine-reviews.json`, Codex on gpt-6-astra at xhigh effort by default), changed the same way with `reviews` as the first word; the other plugins never read it. The dashboard's Codex/Claude toggle with its model and effort pickers, and Slack's `!engine`, `!model` and `!effort`, do the same. Codex's model list is the one its CLI caches (`~/.codex/models_cache.json`); Claude's is its aliases (fable, opus, sonnet, haiku) and full ids. How each engine is locked down for unattended runs is described in `~/.agents/README.md` ("Background agents: the hub") and `core/engine.py`.

## Dashboard

`agents open`, or http://127.0.0.1:8765 while the hub runs. One page, refreshed every few seconds:

- Header: which agent runs (with the Codex/Claude toggle), the Slack connection and the bot's current name, how many jobs are running out of the slots, how long the hub has been up, and a box for any control command.
- One card per plugin: on or paused (click to change), its live facts (last poll, errors, whether it is on call, and so on), and this week's counts.
- The job feed, newest first, filterable by plugin and state: what each job is, how long it took, the engine, tokens or cost, and a link to what it produced (the Slack answer, the posted review, the on-call report).
- A job's detail: summary, error, link, the command to resume its session on the laptop (with a copy button), retry or cancel, and the agent's work as it streams (commands run, tools called, refusals, its messages; thinking and raw tool output stay in the run folder).

It listens on 127.0.0.1 only. Every request must name the dashboard in its Host header, and every change (POST) needs the token from `/api/token` in an `X-Hub-Token` header plus no Origin or the dashboard's own. Another website open in the browser can send a request but cannot read the token, so it cannot change anything. The token is new at each start and kept in `~/.agents/state/hub/dashboard.token` (readable only by this user), where the `agents` command finds it.

## Control commands in Slack

In the bot's DM, a message from Aakif that starts with `!` is a command, never a question for an agent; the answer comes back in the thread.

```
!status                 what is running, queued and failed, per plugin
!jobs [plugin]          the last jobs
!job <id>               one job: state, link, error, resume command
!engine [codex|claude] [--effort E] [--model M]
!engine reviews ... / !model reviews ... / !effort reviews ...   the same, for PR reviews only
!retry <id>             run a finished job again
!cancel <id>            stop a running or queued job
!pause <plugin>         stop starting new jobs for it (its sources keep queueing)
!resume <plugin>
!<plugin> <command> ... a plugin's own command
!help
```

A job id can be shortened to any unique prefix. The prefix is `slack.control_prefix` in `config.json`.

## The `agents` command

```sh
agents status                 engine, plugins, Slack, running and queued jobs
agents check                  tokens, sign-ins, CLIs, per plugin; posts nothing
agents engine [codex|claude] [--model M] [--effort E]
agents jobs [plugin]          the last jobs; also: job <id>, retry <id>, cancel <id>, pause <plugin>, resume <plugin>
agents open                   the dashboard in the browser
agents watch [--plugin NAME]  live view in this terminal; --once prints the status and the last 15 jobs
agents <plugin> <cmd> ...     a plugin's one-shot command, run in this terminal (agents <plugin> lists them)
```

Commands that change the running hub go to it through the dashboard's local API. With the hub stopped, `status` and `jobs` read the job store directly. `agents watch` reads the store and the run folders directly, so it works either way and never changes anything.

## Setup

Done once. `bin/agents` is linked into `~/.local/bin` by `install.sh`.

```sh
# The Python environment (Python 3.12, slack_sdk only)
/Users/aakifmohamed/.pyenv/versions/3.12.8/bin/python3 -m venv ~/.agents/hub/.venv
~/.agents/hub/.venv/bin/pip install slack_sdk

agents check                                                       # tokens, Slack, CLIs, each plugin; posts nothing
cp ~/.agents/hub/ai.envoy.agents-hub.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.envoy.agents-hub.plist   # start now and at every login
```

```sh
launchctl kickstart -k gui/$(id -u)/ai.envoy.agents-hub                              # restart, e.g. after a code change
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.envoy.agents-hub.plist     # stop
tail -f ~/.agents/state/hub/hub.log                                                  # the hub's log
```

The LaunchAgent restarts the hub after a crash, not after a clean exit. Its own output goes to `~/.agents/state/hub/launchd.log`.

## Where state lives

Everything is under `~/.agents/state/hub/` (not in git):

- `hub.sqlite3`: the job queue and history, and each plugin's key-value scope (`kv` table, scope = plugin name; pauses live under scope `hub`).
- `runs/<job id>/`: one folder per job, with the agent's `events.jsonl` and any scratch files the plugin keeps.
- `plugins/<name>/`: a plugin's own files.
- `status.json`: the hub's heartbeat, written every 10 seconds; `agents status` and `agents watch` read it to tell whether the hub runs.
- `hub.log`, `launchd.log`, `dashboard.token`.

Nothing is deleted automatically.

The on-call notebook lives beside it, in `~/.agents/state/on-call/`: `notes.jsonl` (append-only, kept forever) and `notebook.md` (the same, rendered: this shift first). See "The on-call notebook" below.

## The on-call notebook

One record of what each Datadog page turned out to be, shared by the hub and every Claude or Codex session on this Mac (`hub/core/oncall_notes.py`, stdlib only). Entries are shift marks, investigations and notes. The `on_call` watcher marks each shift with its real start from Datadog's on-call schedule; every page investigation reads the notebook first (this shift's entries about the paging monitors in full, then the rest of the shift, then earlier shifts' entries about the same monitors) and saves its report after. A local `/on-call` run does the same through the agents-hub tools `oncall_notes` and `oncall_note`; Slack answers read it with `python3 ~/.agents/hub/core/oncall_notes.py show`. By hand: `agents oncall`, `agents oncall search <words>`, `agents oncall note <text> [--monitor "ID name"]`, and the same as `!oncall ...` in Slack. Counts and ids only, never customer message text.

## Tests

```sh
cd ~/.agents && PYTHONPATH=. hub/.venv/bin/python -m unittest discover -s hub/tests -t .
```

## Known limits

- The Mac must be awake and online. Slack retries a missed event a few times, then drops it; the on-call and PR-review sources catch up from their checkpoints after a sleep.
- Jobs run on threads in one process: a hub restart stops every running job (they are marked failed, to retry), and a plugin bug that crashes the process stops them all until launchd restarts it.
- The dashboard has no login of its own: anyone who can run programs as this user on this Mac can use it, the same as they could run the agents directly.
- `agents watch` and the dashboard show the agent's work as the engine reports it; a step the engine does not report is not shown.

## Buttons, approved actions, the sweep, and staying awake

- Approve-to-act buttons (`core/actions.py`): the on-call report's follow-ups ("Do 1", "Do 2"...), actions the Slack bot proposes (`PROPOSED:` lines in its answer) and drafted PR reviews ("Post this review", "Skip") come with buttons. A tap by Aakif, once, within a week, queues a job; the button is replaced by a "started" note.
- Approved actions run in act mode (`core/agent.py`): Codex from its own home (`~/.agents/state/codex-home-act`) that may write only in worktrees under `~/dev/.claude/worktrees`, the repos' git data, the temp folder and tools' caches, reads AGENTS.md, and may push a branch, open a draft PR and write Linear tickets and comments; Claude with `core/act-claude-settings.json` and `ACT_CLAUDE_TOOLS`. `core/act.rules` refuses merging, approving, closing, `gh api`, force-pushing, deleting branches, `git stash`/`reset`/`clean`, skipping hooks and `az`. Git's hooks go through `core/git-hooks/`, whose pre-push refuses any push that would rewrite or delete a remote branch however it is typed, then runs the repo's own hooks. A push with `--no-verify` skips hooks; the rules and the instructions are what stop that.
- `linear_sweep` moves Aakif's tickets to the state their code reached, on its own (his call, 2026-11-12), after every staging or production promotion in backend, web and the extension (checked every 10 minutes) and daily at 09:30, then DMs what moved; a quiet daily run says nothing. `!linear_sweep run` runs one now.
- Slack's agent view (`features.agent_view` in the manifest): the bot shows as an agent in Slack's Messages tab with suggested prompts, each request shows Slack's working state, and its stop button cancels the running job. Slack shows no custom status text there.
- The hub keeps the Mac awake while it is on the charger (`caffeinate -s` for as long as the hub runs; `power.stay_awake_on_charger` in config.json). Closing the lid still sleeps it unless an external display keeps it open.

## From your own Claude and Codex sessions: the agents-hub MCP server

`hub/mcp_server.py` is an MCP server (stdio) registered for both CLIs from `mcp/servers.json` ("agents-hub"; `python3 mcp/render.py claude --apply` and `codex --apply`). It gives a session seven tools, each a call to the running hub's local API except the notebook's two, which read and write the file directly (so they work with the hub stopped):

- `slack_me`: DM Aakif now from the bot.
- `remind_me`: a reminder the `reminders` plugin posts when due (in 2h, at 3pm, tomorrow 9am, fri 10:30, 2026-11-14 09:00), with snooze buttons. Also `!remind`, `!reminders`, `!unremind` in Slack.
- `run_in_background`: a task for the `background` plugin, run read-only in the folder the session is in (when that is a repo or worktree the hub knows), its answer in his DM, with buttons for any changes it proposes. `allow_changes` makes it ask for an OK in Slack first, then run in act mode. Also `!bg [--changes] <task>`.
- `hub_status`, `hub_job`: what the hub is doing, one job's result.
- `oncall_notes`, `oncall_note`: read the on-call notebook (this shift first; `monitors` adds earlier entries about them, `query` searches) and add to it.

The hub's own runs never load it (their Codex homes list their servers, and PR reviews turn it off), so an agent can't queue work for itself.

## Your own sessions

`core/sessions.py` reads Aakif's saved Claude Code and Codex sessions (VS Code, desktop or terminal; Claude's in `~/.claude/projects`, Codex's in `~/.codex/sessions`): `python3 ~/.agents/hub/core/sessions.py list [--hours 72] [--repo NAME]` and `show <id> [--tail 60]`. The hub's own runs (`claude -p`, `codex exec`, Codex's automatic-review helpers) are left out unless `--all`. The Slack bot and background tasks are told about it, so "what's my backend session doing?" in Slack gets an answer from the transcript. Nothing is sent anywhere; it reads files on this Mac. There is no live list: a session shows as recent from when its file last changed.

