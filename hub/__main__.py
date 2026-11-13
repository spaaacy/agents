"""`agents`: the agents hub from a terminal.

  agents serve                 run the hub (what the LaunchAgent runs)
  agents status                engine, plugins, Slack, running and queued jobs
  agents check                 tokens, sign-ins, CLIs, per plugin; posts nothing
  agents engine [codex|claude] [--model M] [--effort E]
  agents model [name] [--effort E]   list the models, or pick one (sol, astra, opus...; picks its engine too)
  agents effort [level|default]      the current model's effort (low, medium, high, xhigh, max; ultra on some Codex models)
  agents engine|model|effort reviews ...   the same, for PR reviews only (their own setting)
  agents jobs [plugin] | job <id> | retry <id> | cancel <id> | pause <plugin> | resume <plugin>
  agents open                  the dashboard in the browser
  agents watch [--once]        live view of jobs in this terminal
  agents <plugin> <cmd> ...    a plugin's one-shot command (e.g. agents slack_ask ask "...")

Commands that change a running hub (retry, cancel, pause, resume) go to it
through the dashboard's local API; with the hub stopped they act on the job
store directly where they can.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from .core import engine
from .core.hub import STATE_DIR, STATUS_FILE, Hub, load_config

LOG_FILE = STATE_DIR / "hub.log"
TOKEN_FILE = STATE_DIR / "dashboard.token"


def setup_logging(to_file: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if to_file:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        handlers = [logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=5)]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    logging.getLogger("slack_sdk").setLevel(logging.WARNING)


def dashboard_url(cfg: dict) -> str:
    dash = cfg.get("dashboard", {})
    return f"http://{dash.get('host', '127.0.0.1')}:{dash.get('port', 8765)}"


def hub_alive() -> dict | None:
    try:
        status = json.loads(STATUS_FILE.read_text())
    except (OSError, ValueError):
        return None
    return status if time.time() - status.get("heartbeat_at", 0) < 60 else None


def send_command(cfg: dict, words: list[str]) -> str | None:
    """Run a control command in the live hub; None when it isn't reachable."""
    try:
        token = TOKEN_FILE.read_text().strip()
        request = urllib.request.Request(
            dashboard_url(cfg) + "/api/command", data=json.dumps({"words": words}).encode(),
            headers={"Content-Type": "application/json", "X-Hub-Token": token}, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())["reply"]
    except Exception:  # noqa: BLE001
        return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = args[0], args[1:]
    if cmd == "engine":
        return engine.main(rest)
    if cmd == "model":
        return engine.model_main(rest)
    if cmd == "effort":
        return engine.effort_main(rest)
    cfg = load_config()
    if cmd == "serve":
        setup_logging(to_file=True)
        Hub(cfg).serve()
        return 0
    setup_logging(to_file=False)
    if cmd == "open":
        webbrowser.open(dashboard_url(cfg))
        return 0
    if cmd == "watch":
        from .core.watch import main as watch_main

        return watch_main(rest)
    if cmd in ("status", "jobs", "job", "retry", "cancel", "pause", "resume"):
        reply = send_command(cfg, [cmd, *rest])
        if reply is None:
            if cmd in ("cancel",):
                print("the hub isn't running, so nothing is running to cancel")
                return 1
            hub = Hub(cfg, with_slack=False)  # offline: the store and the plugins' config, no sources started
            reply = hub.command([cmd, *rest])
            if cmd == "status":
                reply = "the hub is NOT running (agents serve / the LaunchAgent)\n" + reply
        print(reply)
        return 0
    if cmd == "check":
        hub = Hub(cfg)
        ok = True
        print(f"agents run on {engine.describe()}")
        print(engine.runs_on("reviews"))
        for name, binary in (("codex", engine.CODEX), ("claude", engine.CLAUDE)):
            found = Path(binary).exists()
            ok &= found or name not in (engine.engine(), engine.engine(scope="reviews"))
            print(f"{name} CLI: {'found' if found else 'MISSING'} ({binary})")
        if hub.slack is not None:
            try:
                connected = hub.slack.connect_web()
                print(f"slack: {'signed in as ' + str(hub.slack.bot_name) if connected else hub.slack.error}"
                      f"; bot DM {'open' if hub.slack.bot_dm else 'unavailable'}"
                      f"; answers as you in other DMs {'on' if hub.slack.dm_as_user else 'off'}")
                ok &= connected
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"slack: sign-in failed: {exc}")
        for name, plugin in hub.plugins.items():
            for check in plugin.checks():
                ok &= check.ok
                print(f"{name}: {check.name}: {'ok' if check.ok else 'FAIL'}{' - ' + check.detail if check.detail else ''}")
        print(f"hub: {'running' if hub_alive() else 'not running'}; dashboard {dashboard_url(cfg)}")
        return 0 if ok else 1
    hub = Hub(cfg, with_slack=False)
    if cmd in hub.plugins:
        commands = hub.plugins[cmd].cli()
        if not rest or rest[0] not in commands:
            print(f"{cmd} commands: {', '.join(commands) or 'none'}")
            return 2
        return commands[rest[0]](rest[1:])
    print(f"unknown command {cmd!r}\n\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
