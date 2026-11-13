"""The agents hub: one process for every background agent on this Mac.

It loads config.json, opens the job store and the one Slack connection,
starts each configured plugin's sources, runs jobs on a few worker threads,
serves the local dashboard, and answers the owner's control commands from
Slack and the dashboard. Plugins are listed in config.json as
"module:Class" under "plugins", so a new agent is a new module and one line.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import engine
from .plugin import Plugin
from .scheduler import Scheduler
from .slack import SlackService
from .store import Store

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "config.json"
STATE_DIR = Path.home() / ".agents" / "state" / "hub"
STATUS_FILE = STATE_DIR / "status.json"
HEARTBEAT_SECONDS = 10
log = logging.getLogger("hub")


def load_config(path: Path = CONFIG_FILE) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(path)


class Hub:
    def __init__(self, cfg: dict[str, Any] | None = None, state_dir: Path = STATE_DIR, *, with_slack: bool = True) -> None:
        self.cfg = cfg or load_config()
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(state_dir / "hub.sqlite3")
        self.stopping = threading.Event()
        self.started_at = time.time()
        self.owner = self.cfg["owner"]["slack_user_id"]
        self.slack = SlackService(self.cfg.get("slack", {}), self.owner) if with_slack else None
        self.scheduler = Scheduler(self, int(self.cfg.get("scheduler", {}).get("slots", 2)))
        from .actions import Actions

        self.actions = Actions(self)
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self.plugins: dict[str, Plugin] = {}
        self.broken: dict[str, str] = {}  # plugins that failed to load, and why; the rest still run
        for name, spec in self.cfg.get("plugins", {}).items():
            if not spec.get("enabled", True):
                continue
            try:
                self.plugins[name] = self._load_plugin(name, spec)
            except Exception as exc:  # noqa: BLE001 - one broken plugin must not stop the others
                log.exception("plugin %s failed to load", name)
                self.broken[name] = f"{type(exc).__name__}: {exc}"

    def _load_plugin(self, name: str, spec: dict[str, Any]) -> Plugin:
        module_name, _, class_name = spec["class"].partition(":")
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        # A plugin's own folder holds its config.json; config.json here can override single keys.
        own = Path(module.__file__).parent / "config.json"
        config = {**(json.loads(own.read_text()) if own.exists() else {}), **spec.get("config", {})}
        plugin = cls(self, config)
        if plugin.name != name:
            raise ValueError(f"config key {name!r} loads plugin named {plugin.name!r}")
        return plugin

    # ------------------------------------------------------------------ running

    def serve(self) -> None:
        for job in self.store.recover_interrupted():
            log.warning("job %s (%s) was interrupted by the last stop; marked failed", job.id, job.plugin)
        if self.slack is not None and not self.slack.start():
            log.error("Slack is not connected: %s. Plugins that need it will report so.", self.slack.error)
        self.actions.start()
        self.stay_awake()
        for plugin in self.plugins.values():
            try:
                plugin.start()
            except Exception:  # noqa: BLE001 - one plugin's setup must not stop the others
                log.exception("plugin %s failed to start", plugin.name)
        self.scheduler.start()
        if self.cfg.get("dashboard", {}).get("enabled", True):
            from .dashboard import serve_dashboard

            serve_dashboard(self)
        if self.slack is not None:
            from .controls import SlackControls

            SlackControls(self).start()
            from .home import HomeTab

            HomeTab(self).start()
        log.info("hub running: %s; agents on %s", ", ".join(self.plugins) or "no plugins", engine.describe())
        while not self.stopping.is_set():
            write_json(STATUS_FILE, self.snapshot())
            self.stopping.wait(HEARTBEAT_SECONDS)

    def slack_web(self) -> SlackService | None:
        """The Slack Web API for a one-shot terminal command (no event socket); None when it can't sign in."""
        if self.slack is None:
            self.slack = SlackService(self.cfg.get("slack", {}), self.owner)
        if self.slack.web is None and not self.slack.connect_web():
            return None
        return self.slack

    def stay_awake(self) -> None:
        """Keep the Mac from sleeping while it is on the charger, for as long as the hub runs (`caffeinate -s`
        lasts only on AC power and ends with this process). Closing the lid still sleeps it unless an
        external display keeps it open."""
        if not self.cfg.get("power", {}).get("stay_awake_on_charger", True):
            return
        try:
            subprocess.Popen(["caffeinate", "-s", "-w", str(os.getpid())], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            log.info("keeping the Mac awake while it is on the charger")
        except OSError:
            log.warning("caffeinate is unavailable; the Mac may sleep while the hub runs")

    def wake(self) -> None:
        self.scheduler.wakeup.set()

    def publish(self, event: dict[str, Any]) -> None:
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ what the dashboard and CLI show

    def snapshot(self) -> dict[str, Any]:
        plugins = {}
        for name, plugin in self.plugins.items():
            try:
                live = plugin.status()
            except Exception as exc:  # noqa: BLE001
                live = {"error": f"{type(exc).__name__}: {exc}"}
            plugins[name] = {"title": plugin.title, "priority": plugin.priority, "max_concurrent": plugin.max_concurrent,
                             "paused": plugin.paused, "status": live}
        slack = None
        if self.slack is not None:
            slack = {"connected": self.slack.connected, "bot": self.slack.bot_name, "bot_user_id": self.slack.bot_user_id,
                     "dm_as_user": self.slack.dm_as_user, "error": self.slack.error}
        return {
            "pid": os.getpid(),
            "started_at": self.started_at,
            "heartbeat_at": time.time(),
            "engine": engine.current(),
            "engine_text": engine.describe(),
            "review_engine": engine.current(scope="reviews"),
            "review_engine_text": engine.describe(engine.current(scope="reviews")),
            "engine_options": engine.options(),
            "slots": self.scheduler.slots,
            "running": self.scheduler.running(),
            "slack": slack,
            "plugins": plugins,
            "broken": self.broken,
            "counts": self.store.counts(),
        }

    # ------------------------------------------------------------------ control commands (Slack and dashboard)

    def command(self, words: list[str]) -> str:
        """Run one control command; returns the reply. Shared by `!` messages in Slack and the dashboard."""
        from .controls import run_command

        return run_command(self, words)
