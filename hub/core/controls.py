"""Control commands for the hub, from Slack and the dashboard.

In the bot's DM, a message from the owner that starts with the control prefix
("!" by default) is a command, never a question for an agent:

  !status                 what is running, queued and failed, per plugin
  !jobs [plugin]          the last jobs
  !job <id>               one job: state, link, error, resume command
  !engine [codex|claude] [--effort E] [--model M]
  !model [name] [--effort E]   list the models, or pick one (sol, astra, opus...)
  !effort [level|default]
  !engine reviews ... / !model reviews ... / !effort reviews ...   the same, for PR reviews only
  !retry <id>             run a finished job again
  !cancel <id>            stop a running or queued job
  !pause <plugin>         stop starting new jobs for it (its sources keep queueing)
  !resume <plugin>
  !remind <when> <what>   a reminder in this DM (when: in 2h, at 3pm, tomorrow 9am, fri 10am, 2026-11-14 09:00)
  !reminders              the pending ones; !unremind <id> drops one
  !bg <task>              run a task in the background, read-only; the answer lands here
  !bg --changes <task>    a task that may change code or tickets: asks for your OK first
  !<plugin> <command> ... a plugin's own command
  !help

The dashboard posts the same words to /api/command.
"""
from __future__ import annotations

import logging
import shlex
import time
from typing import TYPE_CHECKING, Any

from . import agent, engine

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger("hub.controls")
HELP = "\n".join(line.strip() for line in (__doc__ or "").splitlines() if line.startswith("  !"))


def ago(at: float | None) -> str:
    if not at:
        return "-"
    seconds = int(time.time() - at)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return f"{seconds}s ago"


def job_line(job: Any) -> str:
    bits = [f"`{job.id}`", job.status, job.plugin, job.title[:80]]
    if job.link:
        bits.append(job.link)
    if job.error and job.status != "done":
        bits.append(f"({job.error[:120]})")
    return " · ".join(bits)


def run_command(hub: Hub, words: list[str]) -> str:
    if not words or words[0] in ("help", "?"):
        return HELP
    cmd, args = words[0].lower(), words[1:]
    store = hub.store
    if cmd == "status":
        lines = [f"agents run on {engine.describe()} · {len(hub.scheduler.running())} running",
                 engine.runs_on("reviews")]
        counts = store.counts()
        for name, plugin in hub.plugins.items():
            c = counts.get(name, {})
            state = "paused" if plugin.paused else "on"
            lines.append(f"{plugin.title or name} ({name}, {state}): {c.get('running', 0)} running, "
                         f"{c.get('queued', 0)} queued, {c.get('failed', 0)} failed this week")
        for name, why in getattr(hub, "broken", {}).items():
            lines.append(f"{name}: NOT LOADED ({why[:200]})")
        if hub.slack is not None and not hub.slack.connected:
            lines.append(f"slack: not connected ({hub.slack.error or 'reconnecting'})")
        return "\n".join(lines)
    if cmd == "jobs":
        jobs = store.jobs(plugin=args[0] if args else None, limit=10)
        return "\n".join(job_line(j) for j in jobs) or "no jobs yet"
    if cmd == "job" and args:
        job = store.find(args[0])
        if not job:
            return f"no single job matches {args[0]}"
        lines = [job_line(job), f"queued {ago(job.created)}, finished {ago(job.finished)}"]
        if job.summary:
            lines.append(job.summary[:500])
        if job.session:
            result = agent.AgentResult(job.engine or "codex", session=job.session)
            lines.append(f"resume: `{result.resume_command()}`")
        return "\n".join(lines)
    if cmd in ("engine", "model", "effort"):
        scope, args = engine.split_scope(args)
    if cmd == "engine":
        if not args:
            return engine.runs_on(scope)
        name, model, effort = args[0], None, None
        rest = args[1:]
        for flag in ("--model", "--effort"):
            if flag in rest and rest.index(flag) + 1 < len(rest):
                value = rest[rest.index(flag) + 1]
                model, effort = (value, effort) if flag == "--model" else (model, value)
        try:
            engine.choose(name, model, effort, scope=scope)
        except ValueError as exc:
            return str(exc)
        return f"{engine.runs_on(scope, now=True)}; the next job uses it"
    if cmd == "model":
        if not args:
            return engine.listing(scope=scope)
        effort = args[args.index("--effort") + 1] if "--effort" in args and args.index("--effort") + 1 < len(args) else None
        try:
            engine.set_model(args[0], effort, scope=scope)
        except ValueError as exc:
            return str(exc)
        return f"{engine.runs_on(scope, now=True)}; the next job uses it"
    if cmd == "effort":
        if not args:
            return engine.runs_on(scope)
        try:
            engine.set_effort(args[0], scope=scope)
        except ValueError as exc:
            return str(exc)
        return f"{engine.runs_on(scope, now=True)}; the next job uses it"
    if cmd in ("retry", "cancel") and args:
        job = store.find(args[0])
        if not job:
            return f"no single job matches {args[0]}"
        if cmd == "retry":
            again = store.retry(job.id)
            hub.wake()
            return f"queued `{again.id}` to run {job.id} again" if again else f"{job.id} is still {job.status}"
        return f"cancelling {job.id}" if hub.scheduler.cancel(job.id) else f"{job.id} is {job.status}, nothing to cancel"
    if cmd in ("pause", "resume") and args:
        name = args[0]
        if name not in hub.plugins:
            return f"no plugin {name}; plugins: {', '.join(hub.plugins)}"
        if cmd == "pause":
            store.set_value("hub", f"paused:{name}", True)
            return f"{name} paused: its new jobs wait until `!resume {name}`"
        store.delete_value("hub", f"paused:{name}")
        hub.wake()
        return f"{name} resumed"
    if cmd == "notify" and args:
        if hub.slack is None or not hub.slack.dm_owner(" ".join(args)):
            return "couldn't reach Slack to send that"
        return "sent"
    for plugin in hub.plugins.values():
        handler = plugin.aliases().get(cmd)
        if handler:
            return handler(args)
    if cmd in hub.plugins:
        commands = hub.plugins[cmd].commands()
        if not args or args[0] not in commands:
            return f"{cmd} commands: {', '.join(commands) or 'none'}"
        return commands[args[0]](args[1:])
    return f"unknown command {cmd!r}\n\n{HELP}"


class SlackControls:
    """Reads the owner's `!` messages in the bot's DM and answers them in the thread."""

    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        self.prefix = hub.cfg.get("slack", {}).get("control_prefix", "!")

    def is_command(self, event: dict[str, Any]) -> bool:
        slack = self.hub.slack
        return (
            slack is not None
            and event.get("type") == "message"
            and not event.get("subtype")
            and not event.get("bot_id")
            and event.get("user") == self.hub.owner
            and event.get("channel") == slack.bot_dm
            and str(event.get("text", "")).lstrip().startswith(self.prefix)
        )

    def start(self) -> None:
        if self.hub.slack is not None:
            self.hub.slack.subscribe(self.on_event)

    def on_event(self, event: dict[str, Any]) -> None:
        if not self.is_command(event):
            return
        text = str(event.get("text", "")).lstrip()[len(self.prefix):]
        try:
            words = shlex.split(text)
        except ValueError:
            words = text.split()
        try:
            reply = run_command(self.hub, words)
        except Exception as exc:  # noqa: BLE001
            log.exception("control command failed")
            reply = f"that failed: {type(exc).__name__}: {exc}"
        self.hub.slack.post(event["channel"], reply, event.get("thread_ts") or event.get("ts"))
