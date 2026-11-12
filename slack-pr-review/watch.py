#!/usr/bin/env python3
"""Read-only terminal view of the Slack review worker's state and its reviewer's events (Codex or Claude)."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parent
ACTIVE = {"reviewing", "posting"}
CLAUDE_EVENTS = {"system", "assistant", "user", "result"}  # Codex's event types never overlap these


def _claude_view():
    """slack-claude's renderer for Claude's stream-json events (thinking and raw tool output left out)."""
    path = Path.home() / ".agents" / "slack-claude" / "watch.py"
    spec = importlib.util.spec_from_file_location("slack_claude_watch", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


claude_view = _claude_view()


def _engine():
    """The shared Codex/Claude switch (~/.agents/engine/engine.py), for the status line."""
    path = Path.home() / ".agents" / "engine" / "engine.py"
    spec = importlib.util.spec_from_file_location("agents_engine", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGINE = _engine()


def clean(value: object) -> str:
    # External text must not be interpreted as terminal control sequences.
    value = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", str(value))
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return "".join(c for c in value if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) < 160))


def emit(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {clean(message)}", flush=True)


def render(event: dict) -> str | None:
    kind = event.get("type")
    if kind in CLAUDE_EVENTS:
        if kind == "result" and isinstance(event.get("structured_output"), dict):
            draft = event["structured_output"]
            lines = [f"Review draft ({draft.get('status', 'ready')}):", str(draft.get("body", ""))]
            for comment in draft.get("comments", []):
                lines.append(f"\n{comment.get('path', '?')}:{comment.get('line', '?')}\n{comment.get('body', '')}")
            return "\n".join(lines)
        return "\n".join(claude_view.render(event)) or None
    item = event.get("item", {})
    item_kind = item.get("type")
    if kind == "thread.started":
        return f"Codex session: {event.get('thread_id', '?')}"
    if kind == "item.completed" and item_kind == "agent_message":
        message = item.get("text", "").strip()
        try:
            draft = json.loads(message)
        except ValueError:
            return message or None
        if isinstance(draft, dict) and "comments" in draft and "body" in draft:
            lines = [f"Review draft ({draft.get('status', 'ready')}):", str(draft["body"])]
            if draft.get("reason"):
                lines.append(str(draft["reason"]))
            for comment in draft["comments"]:
                lines.append(f"\n{comment.get('path', '?')}:{comment.get('line', '?')}\n{comment.get('body', '')}")
            return "\n".join(lines)
        return message or None
    if kind == "item.started" and item_kind == "command_execution":
        command = " ".join(clean(item.get("command", "")).split())
        return f"Running: {command[:500]}{' …' if len(command) > 500 else ''}"
    if kind == "item.completed" and item_kind == "command_execution":
        return f"Command finished (exit {item.get('exit_code', '?')})."
    if item_kind == "mcp_tool_call" and kind in {"item.started", "item.completed"}:
        action = "Calling" if kind == "item.started" else f"Tool {item.get('status', 'finished')}"
        return f"{action}: {item.get('server', '?')}.{item.get('tool', '?')}"
    if kind in {"turn.failed", "error"}:
        error = event.get("error", event)
        return f"Codex error: {error.get('message', error)}"
    if kind == "turn.completed":
        return "Codex turn finished."
    # Reasoning events and raw command/tool output are intentionally not rendered.
    return None


class EventFile:
    def __init__(self, path: Path, recent: bool = False):
        self.path = path
        self.offset = 0
        self.recent = recent
        self.identity = None

    def read(self) -> list[str]:
        try:
            with self.path.open("rb") as source:
                stat = self.path.stat()
                identity = (stat.st_dev, stat.st_ino)
                if self.identity != identity or stat.st_size < self.offset:
                    self.offset = 0
                self.identity = identity
                if self.recent and self.offset == 0 and stat.st_size > 262144:
                    source.seek(stat.st_size - 262144)
                    source.readline()  # Discard a partial line at the cut.
                    self.offset = source.tell()
                source.seek(self.offset)
                data = source.read()
        except FileNotFoundError:
            return []
        end = data.rfind(b"\n") + 1
        self.offset += end  # Leave an incomplete event for the next poll.
        messages = []
        for line in data[:end].splitlines():
            try:
                message = render(json.loads(line))
            except (ValueError, TypeError, AttributeError):
                continue
            if message:
                messages.append(message)
        if self.recent:
            messages = messages[-12:]
            self.recent = False
        return messages


def snapshot(root: Path) -> tuple[dict, list[dict], dict, list[dict]]:
    database = root / "state/state.sqlite3"
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=2) as connection:
        connection.row_factory = sqlite3.Row
        settings = dict(connection.execute("SELECT key, value FROM settings"))
        jobs = [dict(row) for row in connection.execute("SELECT * FROM jobs ORDER BY updated DESC LIMIT 20")]
        counts = dict(connection.execute("SELECT status, count(*) FROM requests GROUP BY status"))
        failures = [dict(row) for row in connection.execute(
            "SELECT id, repo, number, error FROM requests WHERE status='failed' ORDER BY id DESC LIMIT 5")]
    return settings, jobs, counts, failures


def job_summary(job: dict) -> str:
    url = f"https://github.com/{job['repo']}/pull/{job['number']}"
    summary = f"{job['repo']} #{job['number']} — {job['status']}\n{url}"
    if job.get("review_url"):
        summary += f"\nPosted review: {job['review_url']}"
    elif job["status"] == "drafted":
        summary += "\nDraft saved; no GitHub review has been posted for this run."
    if job.get("error"):
        summary += f"\nError: {job['error']}"
    for name in ("review.md", "draft.md"):
        path = Path(job["directory"]) / name
        if path.exists():
            summary += f"\n{name}: {path}"
    if job.get("session"):
        if job.get("reviewer") == "claude":
            checkout = Path(job["directory"]) / "checkout"
            summary += f"\nOpen saved conversation: cd {checkout} && claude --resume {job['session']}"
        else:
            summary += f"\nOpen saved conversation: codex resume {job['session']}"
    return summary


def watch(root: Path, once: bool = False) -> None:
    print("\nSLACK → PR REVIEWS\n", flush=True)
    print("Live view of the automatic review worker. Closing this window only closes the view.\n"
          "Shows progress, commands, findings, and GitHub posting results.\n"
          "Full command output is saved beside each review. Ctrl-C closes this view.\n", flush=True)
    known = {}
    followers = {}
    failed = {}
    last_health = None
    last_heartbeat = 0
    read_error = None
    first = True
    while True:
        try:
            settings, jobs, counts, failures = snapshot(root)
        except (sqlite3.Error, OSError) as error:
            if str(error) != read_error:
                emit(f"Waiting for worker state: {error}")
                read_error = str(error)
            if once:
                return
            time.sleep(2)
            continue
        if read_error:
            emit("Worker state is readable again.")
            read_error = None
        now = time.time()
        enabled = bool(settings.get("enabled_at"))
        auto_post = settings.get("mode") == "post" and bool(settings.get("posting_authorized_at"))
        scanned = float(settings.get("last_scan_ok", 0))
        stale = now - scanned > 180
        queued, running = counts.get("queued", 0), counts.get("running", 0)
        health = (enabled, auto_post, stale, settings.get("scan_error", ""), queued, running, settings.get("reviewer"))
        if health != last_health or now - last_heartbeat >= 60:
            mode = ("auto-post COMMENT reviews" if auto_post else "draft only") if enabled else "disabled"
            scan_time = time.strftime("%H:%M:%S", time.localtime(scanned)) if scanned else "never"
            reviewer = ENGINE.describe()
            emit(f"#devs — {mode}; reviewing with {reviewer}; {queued} queued, {running} running; "
                 f"last successful scan {scan_time}."
                 + (" Scan is overdue; worker may be stopped or recovering." if stale and enabled else "")
                 + (f"\nSlack scan error: {settings['scan_error']}" if settings.get("scan_error") else ""))
            last_health, last_heartbeat = health, now
        if first and jobs:
            emit("Latest run:\n" + job_summary(jobs[0]))
        for job in reversed(jobs):
            key = job["key"]
            signature = (job["status"], job.get("review_url"), job.get("error"))
            changed = known.get(key) != signature
            if key not in known and (not first or job["status"] in ACTIVE):
                followers[key] = [EventFile(Path(job["directory"]) / f"{phase}.events.jsonl", recent=first)
                                  for phase in ("review", "draft")]
            if changed and not first and job["status"] in ACTIVE:
                emit(job_summary(job))
            for stream in followers.get(key, []):
                for message in stream.read():
                    phase = "/review" if stream.path.name.startswith("review.") else "/post-review"
                    emit(f"[{job['repo']} #{job['number']} · {phase}] {message}")
            if changed and not first and job["status"] not in ACTIVE:
                emit(job_summary(job))
            known[key] = signature
            if job["status"] not in ACTIVE:
                followers.pop(key, None)
        for failure in reversed(failures):
            if failed.get(failure["id"]) != failure["error"]:
                emit(f"Request failed: {failure['repo']} #{failure['number']}\n{failure['error']}")
                failed[failure["id"]] = failure["error"]
        first = False
        if once:
            return
        time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--once", action="store_true", help="Print current status and exit")
    args = parser.parse_args()
    try:
        watch(args.root.resolve(), args.once)
    except KeyboardInterrupt:
        print("\nLive view closed. The background worker is still running.", flush=True)
