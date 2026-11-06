#!/usr/bin/env python3
"""Read-only terminal view of the Slack Claude bot: its heartbeat, each request, and Claude's work as it streams."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent / "state"
ACTIVE = {"queued", "answering"}
STALE_AFTER = 45  # seconds without a heartbeat before the bot reads as stopped
TEXT_LIMIT = 700


def clean(value: object) -> str:
    # Slack and tool text must not be read as terminal control sequences.
    value = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", str(value))
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return "".join(c for c in value if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) < 160))


def short(text: object, limit: int = TEXT_LIMIT) -> str:
    text = " ".join(str(text).split()) if limit < 200 else str(text).strip()
    return text if len(text) <= limit else text[:limit] + " …"


def emit(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {clean(message)}", flush=True)


def describe_tool(name: str, args: dict[str, Any]) -> str:
    if name == "Bash":
        return f"Running: {short(args.get('command', ''), 300)}"
    if name == "Read":
        return f"Reading: {args.get('file_path', '?')}"
    if name in {"Grep", "Glob"}:
        where = args.get("path") or ""
        return f"Searching: {short(args.get('pattern', ''), 120)}" + (f" in {where}" if where else "")
    if name.startswith("mcp__"):
        _, server, tool = (name.split("__", 2) + ["", ""])[:3]
        return f"Calling: {server}.{tool} {short(json.dumps(args), 160)}"
    return f"Using {name}: {short(json.dumps(args), 160)}"


def render(event: dict[str, Any]) -> list[str]:
    """What one Claude stream event is worth showing; thinking and raw tool output are left out."""
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        return [f"Claude session {event.get('session_id', '?')}"]
    if kind == "assistant":
        lines = []
        for part in event.get("message", {}).get("content", []) or []:
            if part.get("type") == "text" and part.get("text", "").strip():
                lines.append(f"Claude: {short(part['text'])}")
            elif part.get("type") == "tool_use":
                lines.append(describe_tool(str(part.get("name", "?")), part.get("input") or {}))
        return lines
    if kind == "user":
        lines = []
        for part in event.get("message", {}).get("content", []) or []:
            if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("is_error"):
                content = part.get("content")
                if isinstance(content, list):
                    content = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
                lines.append(f"Tool refused or failed: {short(content, 200)}")
        return lines
    if kind == "result":
        seconds = (event.get("duration_ms") or 0) / 1000
        cost = event.get("total_cost_usd")
        refused = len(event.get("permission_denials") or [])
        return [f"Claude done in {seconds:.0f}s" + (f", ${cost:.2f}" if cost is not None else "") + (f", {refused} tool calls refused" if refused else "")]
    return []


class EventFile:
    """Follows one events.jsonl, returning what was added since the last read."""

    def __init__(self, path: Path, recent: bool = False) -> None:
        self.path = path
        self.offset = 0
        self.recent = recent

    def read(self) -> list[str]:
        try:
            with self.path.open("rb") as source:
                source.seek(self.offset)
                data = source.read()
        except FileNotFoundError:
            return []
        end = data.rfind(b"\n") + 1
        self.offset += end  # an unfinished line waits for the next read
        messages: list[str] = []
        for line in data[:end].splitlines():
            try:
                messages += render(json.loads(line))
            except (ValueError, TypeError, AttributeError):
                continue
        if self.recent:
            messages, self.recent = messages[-12:], False
        return messages


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def where(request: dict[str, Any]) -> str:
    place = "DM, answered as you" if request.get("as_user") else "Slack"
    return f"{place} · {request.get('channel', '?')}"


def summary(request: dict[str, Any]) -> str:
    status = request.get("status", "?")
    lines = [f"{status.upper()} · {where(request)}"]
    if request.get("reply_url"):
        lines.append(f"Answer: {request['reply_url']}")
    elif request.get("mention_url"):
        lines.append(f"Request: {request['mention_url']}")
    if request.get("error"):
        lines.append(f"Error: {request['error']}")
    if request.get("cost_usd") is not None:
        lines.append(f"Cost: ${request['cost_usd']:.2f}" + (f", {request['refused']} tool calls refused" if request.get("refused") else ""))
    if request.get("session_id"):
        lines.append(f"Open the conversation here: claude --resume {request['session_id']}")
    return "\n".join(lines)


def health_line(status: dict[str, Any] | None, now: float) -> str:
    if status is None:
        return "Bot not started yet (no heartbeat). It starts at login; see README.md."
    beat = float(status.get("heartbeat_at", 0))
    if now - beat > STALE_AFTER:
        return f"Bot looks stopped: last heartbeat {time.strftime('%H:%M:%S', time.localtime(beat))}. Check state/launchd.log."
    connection = "connected to Slack" if status.get("connected") else "reconnecting to Slack"
    dms = "answers in other DMs on" if status.get("dm_as_user") else "answers in other DMs off"
    busy = f"answering {status['current']}" if status.get("current") else "idle"
    return f"@claude-aakif — {connection}; {busy}; {status.get('queued', 0)} queued; {dms}."


def watch(root: Path, once: bool = False) -> None:
    print("\nSLACK → CLAUDE (@claude-aakif)\n", flush=True)
    print(
        "Live view of the Slack Claude bot. Closing this window only closes the view; the bot keeps running.\n"
        "Shows each request, what Claude reads and runs, and the answer's link. Ctrl-C closes this view.\n",
        flush=True,
    )
    known: dict[str, tuple[Any, ...]] = {}
    followers: dict[str, EventFile] = {}
    last_health, last_beat_shown = "", 0.0
    first = True
    while True:
        now = time.time()
        line = health_line(read_json(root / "status.json"), now)
        # Shown when it changes, and once a minute as a sign of life.
        if line != last_health or now - last_beat_shown >= 60:
            emit(line)
            last_health, last_beat_shown = line, now
        folders = sorted((root / "requests").glob("*/"))[-30:] if (root / "requests").exists() else []
        if first and folders:
            latest = read_json(folders[-1] / "request.json")
            if latest and latest.get("status") not in ACTIVE:
                emit("Latest request:\n" + summary(latest))
        for folder in folders:
            request = read_json(folder / "request.json")
            if request is None:
                continue
            name = folder.name
            signature = (request.get("status"), request.get("reply_url"), request.get("error"))
            if name not in known and request.get("status") in ACTIVE:
                if not first or request.get("status") == "answering":
                    emit(f"New request ({where(request)}): {short(request.get('text', ''), 300)}")
                followers[name] = EventFile(folder / "events.jsonl", recent=first)
            if name in followers:
                for message in followers[name].read():
                    emit(f"  {message}")
            if known.get(name) != signature and request.get("status") not in ACTIVE and not first:
                emit(summary(request))
                followers.pop(name, None)
            known[name] = signature
        first = False
        if once:
            return
        time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="the bot's state folder")
    parser.add_argument("--once", action="store_true", help="print the current status and exit")
    args = parser.parse_args()
    try:
        watch(args.root.resolve(), args.once)
    except KeyboardInterrupt:
        print("\nLive view closed. The bot is still running.", flush=True)
