# On-call runner

When Datadog pages Aakif while he is on call, this runs the `/on-call` skill by itself and sends the report to his Slack DM from `@claude-aakif`, so it is waiting on his phone next to the page.

Every minute it asks Datadog On-Call whether Aakif is on call for the `devs` team (checked every 5 minutes). Off shift it does nothing. On shift it reads the monitor alert events since its last check; a page is an event that entered a non-OK state and notified `@oncall-devs`, the rule `monitoring/history.py` uses. New pages wait 90 seconds so a burst is investigated together. The runner then posts "Datadog paged you at <time>: <monitors>. Looking into it." in the DM, runs the on-call skill on Codex or Claude, and posts the report in that message's thread with the command to pick up the follow-ups on the laptop (`codex resume <session>` or `claude --resume <session>`, from `~/dev/envoyai-backend`). A monitor that pages again within 2 hours of its investigation gets a "paged again" line in that thread instead of a new run. Replying in the thread goes to the slack-claude bot, which reads the thread (report included) and answers there.

## Codex or Claude

Each investigation runs on whichever agent `agents-engine` names (`~/.agents/bin/agents-engine`; the choice lives in `~/.agents/state/engine.json`). With no choice saved it is Codex, `gpt-6.1-sol` at medium effort. The runner reads the choice when an investigation starts, so a switch applies to the next page with no restart.

```sh
agents-engine            # which agent the background tools use now
agents-engine codex      # or: agents-engine claude
```

## What it can and cannot do

Codex runs from the unattended Codex home that `~/.agents/engine/engine.py` rebuilds before each run (`~/.agents/state/codex-home`). It shares the login and session store with `~/.codex`, so `codex resume <session>` works on the laptop, but none of the interactive config or its rules (which allow git push and commit). In that home every file is read-only, the network is on (gh and Datadog need it), `~/.ssh` is hidden, nothing is ever approved, Linear offers its read tools only, and `engine/background.rules` refuses git pushes and commits, `gh api` and other GitHub writes, PR and issue writes, and `az`. Those rules match command prefixes, so they are a second guard behind the read-only files and the instructions, not a complete wall: a command written to dodge the prefix match would not be caught. The runner hands the Datadog keys to the run's environment, which is how `dd.py` and `history.py` reach Datadog.

Claude runs read-only, the slack-claude bot's way: none of the saved Claude settings (`--setting-sources ""`), `--permission-mode dontAsk` with the allow list in `config.json`, and the deny list in `claude-settings.json`. On top of the slack-claude list it may run `python3 monitoring/history.py`, read a few vendor status pages, and run `dd.py`: read-only Datadog queries (firing monitors, logs, log counts by a field, a metric's series) with the saved keys, because a background run cannot use the Datadog plugin's tools (they need an interactive login). It cannot edit files, commit, push, open PRs, change tickets, mute or resolve anything in Datadog, run `az`, or post anywhere; the runner, not the model, posts to Slack. The skill's final question is replaced by a numbered list of the follow-ups it would have offered (`/tune-monitor`, tickets, the incident report), which Aakif picks after resuming the session.

The Datadog keys come from `~/dev/envoyai-backend/monitoring/.datadog.env` (or `DD_API_KEY` / `DD_APP_KEY` in the environment); the Slack token is the `@claude-aakif` bot token in the Keychain (`slack-claude-bot-token`). Nothing is stored here.

## Live view

`watch-on-call` in any terminal shows the runner's state (on call or not, which agent investigates, idle or investigating, waiting pages, the last poll error), each page it picks up, what the agent reads and runs, and the Slack link and resume command when a run ends. Ctrl-C closes the view; the runner keeps going. `watch-on-call --once` prints the status and exits.

## Setup

Done once:

```sh
cd ~/.agents/on-call-runner
python3 runner.py check      # Datadog keys and team, who is on call, Slack DM, the agent CLIs, the skill
cp ai.envoy.on-call-runner.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.envoy.on-call-runner.plist
```

The runner uses `/Users/aakifmohamed/.pyenv/versions/3.12.8/bin/python3` and the standard library only; it loads two helpers from `../slack-claude` by path.

## Commands

```sh
cd ~/.agents/on-call-runner
python3 runner.py check                  # posts nothing
python3 runner.py preview --hours 24     # the pages it would have picked up
python3 runner.py once --hours 3         # investigate the last 3 hours' pages now, print the report
python3 runner.py once --hours 3 --post  # the same, and post it to the DM
python3 -m unittest -v test_runner.py
tail -f state/runner.log
launchctl kickstart -k gui/$(id -u)/ai.envoy.on-call-runner                              # restart
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.envoy.on-call-runner.plist     # stop
```

## Known limits

- The Mac must be awake and online. After a sleep the runner reads back up to 12 hours of pages it missed (while Aakif is on call) and investigates them then; nothing runs while the lid is closed.
- On its first start it takes only pages from that moment on.
- One investigation at a time, up to 25 minutes each; pages that arrive meanwhile wait for the next one. Each run costs about the same as a long Claude Code or Codex session; a Codex run's footer shows its token count.
- Run folders under `state/runs/` are never deleted automatically.
