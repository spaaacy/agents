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
