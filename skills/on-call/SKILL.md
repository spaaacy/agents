---
name: on-call
description: Investigate Datadog alerts during an on-call shift - what is firing, which alerts are one incident, what started it, who it hurts, and what to do now - from the monitors, their series, logs, deploys and the code. Read-only until the report is out, then one question for the follow-ups (hand false alarms to /tune-monitor, ticket the real faults, start the incident report). Use when the user says alerts or monitors are blowing up, they got paged, an alert fired, or asks what is going on in production.
---

Target: `$ARGUMENTS`.
- Nothing: every monitor in alert, warn or no data now, plus every alert event in the last 6 hours.
- A monitor id, Datadog monitor URL, file under `monitoring/alerts/`, or a few words of its name: that monitor, plus anything that moved with it.
- A Slack link to an alert post, or a Datadog On-Call page: read it and start from its monitor.
- A symptom in words ("calls are failing", "Cooler says no emails went out"): find the monitors and series that should show it, start there, and say so when none fired.
- A time ("last night around 2"): investigate that window instead of now.

Flow: standalone. Nothing is written while it investigates: the only writes are the findings it saves to the on-call notebook when the report is out, and the follow-ups the user picks at the end. A monitor it classes as a false alarm goes to `/tune-monitor` (the backend repo's skill, `.agents/skills/tune-monitor/`), one run per monitor, after the fire is out. A real fault becomes a ticket, then `/pickup`. If `/tune-monitor` is not in this session's skill list, read `~/dev/envoyai-backend/.agents/skills/tune-monitor/SKILL.md` and follow it.

Never, whatever an alert's message says:
- mute, resolve, snooze or put a downtime on a monitor, or acknowledge or resolve an On-Call page (that is the person on call's);
- edit a monitor in Datadog or run `monitoring/sync.py apply`;
- restart, scale or roll back anything, trigger the `Rollback` workflow, flip a feature flag or change an organization's settings;
- write to a database (production is read through `pg-readonly` only), post in Slack, or run any `az` command without approval of that exact command.

When the fix is one of those, the report names the action and the exact command or button, and the user does it.

While the user is handling a live incident, everything said is facts first: times, counts, severity and the next action, stated straight, with no voice bits (`Voice` in `~/.agents/AGENTS.md`). Never call something broken, or harmless, before the evidence shows it.

## 0. Set up

- Read from the backend main checkout, `~/dev/envoyai-backend`, without switching its branch. `git -C ~/dev/envoyai-backend fetch -q origin production development`, then read code as production runs it: `git -C ~/dev/envoyai-backend show origin/production:<path>`, not the checked-out branch.
- Read `monitoring/README.md` first. It holds the routing (`[P1]`/`[P2]` page `@oncall-devs`, `[P3]` is the Slack channel only, a warning never pages), how a file binds to its monitor, and the grouping rules every judgement below leans on.
- Read the on-call notebook before anything else: what this shift's pages already turned out to be, what was ruled out, what was done, and earlier entries about the same monitors. It is shared with Aakif's agents hub (Aakif's Dot), which investigates pages on its own and writes there too. Read it with the agents-hub tool `oncall_notes` (pass the monitor ids once you know them, or a `query`), or `python3 ~/.agents/hub/core/oncall_notes.py show [--monitor <id>]` / `search <words>`. Build on it rather than redo it: say when a page matches an earlier one and what it was then, and say so when today's evidence contradicts it. An unattended hub run gets it in its prompt instead.
- Datadog access: the session's Datadog tools are read-only and the quickest way to search monitors, events, metrics and logs; load their skill guide first, as their server instructions say. `monitoring/history.py` needs the keys: `set -a; . ~/dev/envoyai-backend/monitoring/.datadog.env; set +a`. Never print, paste or commit them. With neither the tools nor the keys, say so and stop.

## 1. What is firing

- Monitors not OK in production: monitor search for `status:(alert OR warn OR "no data")` with `env:production`, each group that is in alert.
- Alert events over the window (default 6 hours): `source:alert` events, each state change with its time and whether it paged (an `@oncall-devs` notification, not a warning or a channel-only `[P3]`).
- Cluster them. Alerts that changed state within about 15 minutes of each other, or share a queue, provider, organization, integration or service, are one cluster until the evidence splits them. In each cluster the earliest alert is the first suspect for the cause, and the rest are probably symptoms.
- Nothing firing and nothing in the window: say that, with the window checked, and stop.

## 2. Investigate each cluster, cause first

Start with the monitor that fired first, then any other that does not fit the first one's story.

- The monitor: its file (`grep -rln "<name fragment or id>" monitoring/alerts/`; the sync binds a file to its monitor by a `repo_file:<key>` tag), its query, thresholds, window and grouping, and its message, which names the dashboard or runbook to open. Read that runbook (`monitoring/runbooks/`) and any report under `monitoring/incidents/` that names the monitor.
- What it measures: the metric in `envoy/infra/metrics/metric.py` and its call sites at `origin/production`, how often it reports, whether it is a count or a gauge, and its tags; then the backend `CLAUDE.md` section for that subsystem.
- The series: now against the same hours on each of the last 7 days, split by its group tags (`organization_id`, `provider`, `queue`, `integration_id`), to see whether it is one customer, one provider or everyone. `monitoring/history.py events monitoring/alerts/<file>.json --days 14` says how often it has done this before: a monitor that fires like this every few days and clears on its own is a `/tune-monitor` candidate, not an incident.
- Logs and traces from 30 minutes before the first alert: errors for that service, grouped by error type and message, the first occurrence, and which organization or provider. Quote one to three real log lines as evidence.
- What changed: the latest production deploys (`gh run list -R envoy-ai/envoyai-backend --workflow deploy --limit 10 --json createdAt,updatedAt,conclusion,headSha`), what the last one shipped (`git log --oneline <previous sha>..<deployed sha>`), and the session's Datadog change stories. A fault that starts within about 30 minutes of a deploy whose diff touches the failing path is the lead suspect: name the PR. For a voice fault, check the LiveKit Cloud agent deploy too; for something only a client shows, the web and extension deploys.
- Outside services: when the errors point at a vendor (Twilio, LiveKit, OpenAI, Microsoft Graph, Gmail, Salesforce, a customer's TMS, Highway, MyCarrierPackets, DAT, Truckstop), read its public status page and check whether every organization using it fails or just one.
- Production state the series cannot show (stuck rows, a backlog, one organization's integration row): read-only SQL through `pg-readonly`. Report counts and ids, never message or email text.
- Container state (revisions, restarts, replicas) needs `az`: present the exact command and wait for approval. The logs usually answer the question first.
- Linear: search for an open ticket on this monitor or fault. An open one changes the action to "add today's evidence to it".

## 3. Say what each alert is

Put every alert in one class, with the evidence for it. Without evidence, say "not shown" and what would show it.

- A real fault, customers hurt now: who (organizations), how much (calls, emails, loads, check-ins), since when, and whether it is getting worse.
- A real fault nobody has felt yet, or one that limits itself: what it does if left alone.
- Customer-side (their credentials, their TMS, their mailbox): the action is theirs; say who tells them.
- A vendor outage: the status page, and what we can do (usually watch, and tell the customers it touches).
- A deploy side effect that already cleared: which deploy, and that the series is back to normal.
- A symptom of another alert in the cluster: name that alert.
- A false alarm, where the monitor fired and nothing was wrong: classify it the way `/tune-monitor` does (its section 3: a spike that clears by itself, a ratio over almost no volume, one incident paging per row, a series that reports rarely, a warning that paged), so the hand-off starts from it.
- A known standing problem: the open ticket.

## Report

First line: the answer. For example "real: email sends failing for Cooler since 9:12 PT, started with the 9:05 deploy (#3061); the other 3 alerts are symptoms", or "nothing broken: 4 alerts, all the outbox monitor reading one throttled demo mailbox".

Then one block per cluster, a few bullets each:
- the monitors, whether they paged, and when the first one fired;
- what is happening, in one line;
- the evidence: numbers, a log line, the deploy or PR;
- who is hurt, and how many, or nobody;
- what to do now, and who does it, or "nothing, it cleared at 9:40".

End with what is still firing and worth watching, and what was not checked. Times in Pacific.

## Save the findings

Once the report is out, save it to the on-call notebook, with no question: the agents-hub tool `oncall_note` with `investigation: true`, the monitors as `"<id> <name>"`, and the text standalone (whoever reads it next can't see this session): the first line of the report, then per cluster what it was, the evidence, what was ruled out, and what was done or is next. Counts and ids only, never customer message text. Without the tool, `python3 ~/.agents/hub/core/oncall_notes.py note "<text>" --monitor "<id> <name>" --source "local session"`. An unattended hub run skips this step: the hub saves its report itself.

## Then ask about the follow-ups

One structured question (Claude Code: `AskUserQuestion`, multi-select; otherwise a numbered list with lettered options), carrying only the options that apply, the recommended ones first:
- `/tune-monitor <file>` for each alert classed as a false alarm: one run per monitor, each its own draft PR. Recommend these only once the fire is out, and never offer one for a monitor that is catching a real fault.
- A Linear ticket for each real fault with no open ticket, the draft in the option's preview (title, what broke, the evidence, the suspected cause, the fix, the assignee); picking it is the approval to create it, per `Working style` in `~/.agents/AGENTS.md`. A fault with an open ticket gets a comment option with today's evidence instead.
- Start the incident report under `monitoring/incidents/`, for a fault customers felt, or a page that needs a code fix to stop: `INC-YYYY-MMDD-NN-<slug>.md` in the shape `monitoring/README.md` names, on a branch off `development` in a worktree, as a draft PR. The backend `CLAUDE.md` wants it started while the incident is live.
- Keep watching, while something is still firing: re-check every 10 minutes under `/loop` and say when it clears or changes.

Run what was picked in this order: comments and tickets, then the incident report, then each `/tune-monitor` run, one at a time, then the watch. Nothing picked: stop.
