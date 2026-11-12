"""What a plugin is. Adding an agent to the hub = one module under hub/plugins/ with a Plugin subclass,
plus an entry in config.json's "plugins".

A plugin has two halves:
- sources, started by start(): whatever notices work (a Slack event, a poll of
  Datadog, a search of #devs) and calls ``self.enqueue(...)``. Sources run in
  their own daemon threads and must never run an agent themselves.
- handle(job, ctx): does one job, normally by running an agent turn through
  ``ctx.run_agent(...)``, and returns a JobResult. The scheduler calls it on one
  of its worker threads, at most ``max_concurrent`` at a time for this plugin,
  highest ``priority`` first across all plugins.

The hub gives every plugin the same things: its config section, the shared
Slack connection (hub.slack), the job store, a key-value scope of its own
(self.get/self.put), a state folder of its own, the engine switch, and a log.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from . import agent
from .store import Job, JobResult

if TYPE_CHECKING:
    from .hub import Hub


@dataclass
class Check:
    """One line of `agents check`."""

    name: str
    ok: bool
    detail: str = ""


class JobContext:
    """What handle() gets besides the job: its run folder, a cancel flag, and the agent runner."""

    def __init__(self, hub: Hub, plugin: Plugin, job: Job, run_dir: Path, cancel: threading.Event) -> None:
        self.hub, self.plugin, self.job, self.run_dir, self.cancel = hub, plugin, job, run_dir, cancel
        self.log = plugin.log

    def run_agent(self, task: agent.AgentTask) -> agent.AgentResult:
        """One agent turn, logged into this job's run folder and recorded on the job as it starts."""
        task.run_dir = task.run_dir or self.run_dir
        name = task.engine or agent.engine.engine()
        self.hub.store.update(self.job.id, engine=name)
        result = agent.run(task, self.cancel)
        self.hub.store.update(self.job.id, session=result.session)
        return result

    def note(self, summary: str) -> None:
        """A progress line the dashboard shows while the job runs."""
        self.hub.store.update(self.job.id, summary=summary)


class Plugin:
    name: str = ""           # config key, job.plugin, kv scope
    title: str = ""          # what people see
    priority: int = 50       # higher runs first
    max_concurrent: int = 1  # jobs of this plugin at once

    def __init__(self, hub: Hub, config: dict[str, Any]) -> None:
        self.hub = hub
        self.config = config
        self.log = logging.getLogger(f"hub.{self.name}")
        self.state_dir = hub.state_dir / "plugins" / self.name
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ to override

    def start(self) -> None:
        """Start this plugin's sources. Default: none."""

    def handle(self, job: Job, ctx: JobContext) -> JobResult:
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        """Live facts for the dashboard and `agents status` (connected, last poll, errors...)."""
        return {}

    def checks(self) -> list[Check]:
        """`agents check` lines: tokens, sign-ins, CLIs. Posts nothing."""
        return []

    def act(self, name: str, args: dict[str, Any], job: Job, ctx: JobContext) -> JobResult:
        """Run an action the owner approved with a button (actions.py). "agent" works for every plugin:
        an agent in act mode does ``args["instruction"]``, resuming ``args["session"]`` when given."""
        if name == "agent":
            return self.run_agent_action(args, job, ctx)
        raise NotImplementedError(f"{self.name} has no action {name!r}")

    def run_agent_action(self, args: dict[str, Any], job: Job, ctx: JobContext) -> JobResult:
        cfg = self.hub.cfg.get("agent", {})
        task = agent.AgentTask(
            prompt=agent.ACT_PREAMBLE + str(args.get("instruction", "")).strip(),
            cwd=Path(args.get("cwd") or cfg.get("workdir", "~/dev/envoyai-backend")).expanduser(),
            session=agent.resumable(args.get("session")), mode="act", timeout=int(args.get("timeout", 2700)),
            mcp_servers=list(cfg.get("mcp_servers", [])) + ["linear", "datadog"],
            add_dirs=list(cfg.get("add_dirs", [])), repos=list(cfg.get("repos", [])),
            claude_allowed_tools=agent.ACT_CLAUDE_TOOLS, claude_settings=agent.ACT_CLAUDE_SETTINGS,
            env=dict(args.get("env") or {}),
        )
        ctx.note("doing it…")
        result = ctx.run_agent(task)
        reply = job.payload.get("reply") or {}
        text = result.text if not result.error else f"Couldn't finish that: {result.error}"
        resume = result.resume_command()
        if resume:
            text += f"\n\n_to pick it up on the laptop:_ `{resume}`"
        link = None
        if reply.get("channel") and self.hub.slack is not None:
            posted = self.hub.slack.post_markdown(reply["channel"], text, reply.get("thread_ts"))
            link = self.hub.slack.permalink(reply["channel"], posted[0]) if posted else None
        first = next((line for line in result.text.splitlines() if line.strip()), "") if result.text else ""
        return JobResult(status="failed" if result.error else "done", summary=first[:300] or None, link=link,
                         error=result.error, engine=result.engine, session=result.session, tokens=result.tokens,
                         cost=result.cost_usd)

    def offer(self, name: str, label: str, args: dict[str, Any] | None = None, *, style: str | None = None,
              confirm: str | None = None) -> dict[str, Any]:
        """A Slack button that runs action ``name`` of this plugin when the owner taps it."""
        return self.hub.actions.button(self.name, name, label, args, style=style, confirm=confirm)

    def commands(self) -> dict[str, Callable[[list[str]], str]]:
        """Extra control commands (Slack `!<name> <cmd>` and the dashboard), each returning a reply line."""
        return {}

    def cli(self) -> dict[str, Callable[[list[str]], int]]:
        """One-shot terminal commands, `agents <plugin> <cmd> ...` (run in that terminal, not the hub); exit codes."""
        return {}

    # ------------------------------------------------------------------ helpers

    def enqueue(self, title: str, payload: dict[str, Any] | None = None, *, dedupe: str | None = None,
                priority: int | None = None) -> Job | None:
        job = self.hub.store.enqueue(self.name, title, payload, priority=self.priority if priority is None else priority,
                                     dedupe=f"{self.name}:{dedupe}" if dedupe else None)
        if job:
            self.log.info("queued %s: %s", job.id, title)
            self.hub.wake()
        return job

    def get(self, key: str, default: Any = None) -> Any:
        return self.hub.store.get_value(self.name, key, default)

    def put(self, key: str, value: Any) -> None:
        self.hub.store.set_value(self.name, key, value)

    def spawn(self, target: Callable[[], None], name: str) -> threading.Thread:
        """A daemon thread for a source loop; an exception is logged and the loop restarted after a pause."""

        def guarded() -> None:
            while not self.hub.stopping.is_set():
                try:
                    target()
                    return
                except Exception:  # noqa: BLE001 - a source must not take the hub down
                    self.log.exception("%s crashed; restarting in 30s", name)
                    self.hub.stopping.wait(30)

        thread = threading.Thread(target=guarded, name=f"{self.name}:{name}", daemon=True)
        thread.start()
        self._threads.append(thread)
        return thread

    @property
    def paused(self) -> bool:
        return bool(self.hub.store.get_value("hub", f"paused:{self.name}", False))
