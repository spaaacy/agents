"""`agents watch`: a live view of the hub in this terminal.

Reads the job store, the run folders and the hub's heartbeat directly, so it
works whether or not the hub is running and never changes anything. Ctrl-C
closes the view; the hub keeps going.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import engine
from .agent import split_session
from .events import clean, read_events
from .hub import CONFIG_FILE, STATE_DIR, STATUS_FILE
from .store import Job, Store

POLL_SECONDS = 2
STALE_AFTER = 60



def fact(value: object) -> str:
    """A plugin's status fact for the terminal: epoch seconds read as "3m ago", the rest as is."""
    if isinstance(value, float) and 1.5e9 < value < 4e9:
        seconds = int(time.time() - value)
        for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
            if seconds >= size:
                return f"{seconds // size}{unit} ago"
        return f"{seconds}s ago"
    return str(value)

def emit(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {clean(message)}", flush=True)


def ago(at: float | None) -> str:
    if not at:
        return "-"
    seconds = int(time.time() - at)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return f"{seconds}s ago"


def heartbeat(path: Path = STATUS_FILE) -> dict[str, Any] | None:
    try:
        status = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return status if time.time() - float(status.get("heartbeat_at", 0)) < STALE_AFTER else None


def plugin_names(status: dict[str, Any] | None) -> list[str]:
    if status:
        return list(status.get("plugins", {}))
    try:
        return list(json.loads(CONFIG_FILE.read_text()).get("plugins", {}))
    except (OSError, ValueError):
        return []


def header(store: Store, status: dict[str, Any] | None) -> list[str]:
    lines = [f"agents run on {engine.describe()} · {engine.runs_on('reviews')}"]
    if status:
        slack = status.get("slack") or {}
        slack_text = ("Slack @" + str(slack.get("bot") or "?")) if slack.get("connected") else \
            ("Slack not connected" + (f": {slack['error']}" if slack.get("error") else "")) if slack else "Slack off"
        lines.append(f"hub up since {ago(status.get('started_at'))} (pid {status.get('pid')}); "
                     f"{len(status.get('running') or [])}/{status.get('slots')} running; {slack_text}")
    else:
        lines.append("hub NOT running (no heartbeat in the last minute): `agents serve` or the LaunchAgent")
    counts = store.counts()
    for name in plugin_names(status):
        live = (status or {}).get("plugins", {}).get(name, {})
        paused = live.get("paused") if status else store.get_value("hub", f"paused:{name}", False)
        c = counts.get(name, {})
        facts = ", ".join(f"{k.replace('_', ' ')} {fact(v)}" for k, v in (live.get("status") or {}).items()
                          if v not in (None, "", [], {}) and not isinstance(v, (dict, list)))
        lines.append(f"  {live.get('title') or name} ({name}): {'PAUSED' if paused else 'on'}; "
                     f"{c.get('running', 0)} running, {c.get('queued', 0)} queued, {c.get('failed', 0)} failed this week"
                     + (f"; {facts}" if facts else ""))
    return lines


def job_line(job: Job) -> str:
    bits = [job.status.upper(), job.id, job.plugin, job.title[:100]]
    if job.engine:
        bits.append(job.engine)
    if job.started:
        bits.append(f"{int((job.finished or time.time()) - job.started)}s")
    if job.tokens:
        bits.append(f"{job.tokens:,} tokens")
    if job.cost is not None:
        bits.append(f"${job.cost:.2f}")
    if job.link:
        bits.append(job.link)
    if job.error and job.status != "done":
        bits.append(f"error: {job.error[:200]}")
    return " · ".join(bits)


def finished_note(job: Job) -> list[str]:
    lines = []
    if job.summary:
        lines.append("  " + job.summary.strip().splitlines()[0][:300])
    if job.payload.get("resume_command"):
        lines.append(f"  resume: {job.payload['resume_command']}")
    elif job.session:
        name, sid = split_session(job.session)
        cmd = f"codex resume {sid}" if name == "codex" else f"claude --resume {sid}"
        lines.append(f"  resume: cd ~/dev/envoyai-backend && {cmd}")
    return lines


def run_once(store: Store, plugin: str | None) -> int:
    for line in header(store, heartbeat()):
        print(clean(line))
    print()
    jobs = store.jobs(plugin=plugin, limit=15)
    if not jobs:
        print("no jobs yet")
    for job in jobs:
        print(clean(f"{ago(job.created):>8}  {job_line(job)}"))
    return 0


def follow(store: Store, plugin: str | None, state_dir: Path) -> int:
    print("AGENTS HUB\n\nLive view of every background agent. Closing this view leaves the hub running. Ctrl-C closes it.\n",
          flush=True)
    seen: dict[str, str] = {}
    offsets: dict[str, int] = {}
    last_header: list[str] = []
    first = True
    while True:
        status = heartbeat()
        head = header(store, status)
        if head != last_header:
            for line in head:
                emit(line)
            last_header = head
        jobs = list(reversed(store.jobs(plugin=plugin, limit=50)))
        for job in jobs:
            before = seen.get(job.id)
            if before != job.status:
                seen[job.id] = job.status
                if first and job.status not in ("queued", "running"):
                    continue  # history: shown by --once, not replayed here
                emit(job_line(job))
                if job.status not in ("queued", "running"):
                    for line in finished_note(job):
                        emit(line)
            if job.status == "running" or (job.id in offsets and before == "running"):
                path = state_dir / "runs" / job.id / "events.jsonl"
                lines, offsets[job.id] = read_events(path, offsets.get(job.id, 0))
                for line in lines:
                    emit(f"  {job.id} {line}")
            if job.status not in ("queued", "running"):
                offsets.pop(job.id, None)
        first = False
        time.sleep(POLL_SECONDS)


def main(argv: list[str] | None = None, state_dir: Path = STATE_DIR) -> int:
    parser = argparse.ArgumentParser(prog="agents watch", description="Live view of the agents hub.")
    parser.add_argument("--once", action="store_true", help="print the status and the last 15 jobs, then exit")
    parser.add_argument("--plugin", help="only this plugin's jobs")
    args = parser.parse_args(argv)
    store = Store(state_dir / "hub.sqlite3")
    try:
        return run_once(store, args.plugin) if args.once else follow(store, args.plugin, state_dir)
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
