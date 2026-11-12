#!/usr/bin/env python3
"""Read-only terminal view of the on-call runner: its heartbeat, each page it picked up, and the agent's work as it streams."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent / "state"
SLACK_CLAUDE_WATCH = Path.home() / ".agents" / "slack-claude" / "watch.py"
STALE_AFTER = 150  # seconds without a heartbeat before the runner reads as stopped (it polls every 60)


def _load_view() -> Any:
    """slack-claude's live view, for its event rendering (thinking and raw tool output left out)."""
    spec = importlib.util.spec_from_file_location("slack_claude_watch", SLACK_CLAUDE_WATCH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SLACK_CLAUDE_WATCH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up while being built
    spec.loader.exec_module(module)
    return module


view = _load_view()


def health_line(status: dict[str, Any] | None, now: float) -> str:
    if status is None:
        return "Runner not started yet (no heartbeat). It starts at login; see README.md."
    beat = float(status.get("heartbeat_at", 0))
    if now - beat > STALE_AFTER:
        return f"Runner looks stopped: last heartbeat {time.strftime('%H:%M:%S', time.localtime(beat))}. Check state/launchd.log."
    shift = {True: "you're on call", False: "you're not on call, pages are ignored", None: "on-call status unknown"}
    parts = [shift[status.get("on_call")]]
    if status.get("engine"):
        parts.append(f"investigating on {status['engine']}")
    parts.append(f"investigating {status['current']}" if status.get("current") else "idle")
    if status.get("pending"):
        parts.append(f"{status['pending']} pages waiting")
    if status.get("last_error"):
        parts.append(f"last poll failed: {status['last_error']}")
    return "On-call runner — " + "; ".join(parts) + "."


def summary(run: dict[str, Any]) -> str:
    lines = [f"{str(run.get('status', '?')).upper()} · {run.get('headline', '')}"]
    if run.get("thread_url"):
        lines.append(f"Slack: {run['thread_url']}")
    if run.get("error"):
        lines.append(f"Error: {run['error']}")
    if run.get("engine"):
        lines.append(f"Agent: {run['engine']}")
    if run.get("cost_usd") is not None:
        lines.append(f"Cost: ${run['cost_usd']:.2f}")
    if run.get("tokens") is not None:
        lines.append(f"Tokens: {run['tokens']:,}")
    if run.get("session_id"):
        if run.get("agent") == "codex":
            lines.append(f"Open it here: cd ~/dev/envoyai-backend && codex resume {run['session_id']}")
        else:
            lines.append(f"Open it here: claude --resume {run['session_id']}")
    return "\n".join(lines)


def watch(root: Path, once: bool = False) -> None:
    print("\nDATADOG PAGES → /on-call\n", flush=True)
    print(
        "Live view of the on-call runner. Closing this window only closes the view; the runner keeps going.\n"
        "Shows each page it picks up, what the agent reads and runs, and the report's Slack link. Ctrl-C closes this view.\n",
        flush=True,
    )
    known: dict[str, tuple[Any, ...]] = {}
    followers: dict[str, Any] = {}
    last_health, last_shown = "", 0.0
    first = True
    while True:
        now = time.time()
        line = health_line(view.read_json(root / "status.json"), now)
        if line != last_health or now - last_shown >= 60:
            view.emit(line)
            last_health, last_shown = line, now
        folders = sorted((root / "runs").glob("*/"))[-30:] if (root / "runs").exists() else []
        if first and folders:
            latest = view.read_json(folders[-1] / "run.json")
            if latest and latest.get("status") != "investigating":
                view.emit("Latest run:\n" + summary(latest))
        for folder in folders:
            run = view.read_json(folder / "run.json")
            if run is None:
                continue
            name = folder.name
            signature = (run.get("status"), run.get("thread_url"), run.get("error"))
            if name not in known and run.get("status") == "investigating":
                view.emit(run.get("headline", name))
                followers[name] = view.EventFile(folder / "events.jsonl", recent=first)
            if name in followers:
                for message in followers[name].read():
                    view.emit(f"  {message}")
            if known.get(name) != signature and run.get("status") != "investigating" and not first:
                view.emit(summary(run))
                followers.pop(name, None)
            known[name] = signature
        first = False
        if once:
            return
        time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="the runner's state folder")
    parser.add_argument("--once", action="store_true", help="print the current status and exit")
    args = parser.parse_args()
    try:
        watch(args.root.resolve(), args.once)
    except KeyboardInterrupt:
        print("\nLive view closed. The runner is still going.", flush=True)
