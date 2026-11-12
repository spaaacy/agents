#!/usr/bin/env python3
"""Read Aakif's own Claude Code and Codex sessions (VS Code, desktop or terminal) from the files both CLIs save.

  python3 ~/.agents/hub/core/sessions.py list [--hours 72] [--repo NAME] [--all]
  python3 ~/.agents/hub/core/sessions.py show <id or its start> [--tail 60]

`list` shows recent sessions, newest activity first: which agent, where it ran (folder and branch), when
it was last active, its first request and its latest one. `show` prints one session as a readable
transcript (his requests, the agent's replies, one line per command or file it touched), the last
--tail entries. The hub's own background runs (Claude's `claude -p`, Codex's `codex exec` and its
automatic-review helpers) are left out unless --all.

Read-only, standard library only: the hub's agents run it to answer "what is my backend session doing?".
Claude saves sessions in ~/.claude/projects/<folder>/<id>.jsonl, Codex in ~/.codex/sessions/YYYY/MM/DD/.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude" / "projects"
CODEX_DIR = HOME / ".codex" / "sessions"
BACKGROUND_CLAUDE = {"sdk-cli", "sdk-ts", "sdk-py"}
BACKGROUND_CODEX = {"codex_exec"}
HEAD_BYTES = 400_000
TAIL_BYTES = 2_000_000
TAG = re.compile(r"<(system-reminder|task-notification|ide_selection|ide_opened_file|command-[a-z-]+|local-command-stdout|"
                 r"user-prompt-submit-hook|environment_context|turn_aborted|pasted_content[^>]*)>.*?</\1[^>]*>", re.S)


@dataclass
class Session:
    engine: str
    id: str
    path: Path
    cwd: str = ""
    branch: str = ""
    client: str = ""
    started: float = 0.0
    last: float = 0.0
    first_request: str = ""
    last_request: str = ""
    background: bool = False
    entries: list[tuple[float, str, str]] = field(default_factory=list)  # (time, who, text) for show


def clean(text: str, limit: int | None = None) -> str:
    text = TAG.sub("", text or "").strip()
    text = re.sub(r"\s+\n", "\n", text)
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def short(text: str, limit: int = 140) -> str:
    return clean(" ".join((text or "").split()), limit)


def when(epoch: float) -> str:
    if not epoch:
        return "?"
    seconds = int(time.time() - epoch)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return "just now"


def stamp(value: Any) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def lines(path: Path, *, head: int | None = None, tail: int | None = None) -> Iterator[dict[str, Any]]:
    """JSON lines from the start (head bytes) or the end (tail bytes) of a file, skipping a cut-off line."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if tail and size > tail:
            handle.seek(size - tail)
            handle.readline()  # most likely half a line
            data = handle.read()
        else:
            data = handle.read(head) if head else handle.read()
    for raw in data.splitlines():
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict):
            yield item


# --------------------------------------------------------------------------- Claude


def claude_text(content: Any) -> tuple[str, list[str]]:
    """(the words in a message, one line per tool it called)."""
    if isinstance(content, str):
        return content, []
    words, tools = [], []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            words.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            args = block.get("input") or {}
            detail = args.get("command") or args.get("file_path") or args.get("pattern") or args.get("url") or ""
            tools.append(f"{block.get('name')}: {short(str(detail), 160)}".rstrip(": "))
    return "\n".join(words), tools


def claude_human(item: dict[str, Any]) -> str | None:
    """His own words in a user entry; None for tool results, hook output and command echoes."""
    if item.get("type") != "user" or item.get("isMeta") or item.get("isSidechain"):
        return None
    content = (item.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    text, _ = claude_text(content)
    name = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
    if name:  # a slash command: show it as he typed it, "/pickup ENG-1234"
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        text = f"{name[1].strip()} {args[1].strip() if args else ''}".strip()
    text = clean(text)
    return text or None


def read_claude(path: Path, full: bool = False) -> Session:
    session = Session("claude", path.stem, path)
    items = list(lines(path)) if full else list(lines(path, head=HEAD_BYTES))
    tail_items = [] if full else list(lines(path, tail=TAIL_BYTES))
    for item in items:
        if item.get("entrypoint") and not session.client:
            session.client = item["entrypoint"]
            session.background = item["entrypoint"] in BACKGROUND_CLAUDE
        if item.get("cwd") and not session.cwd:
            session.cwd = item["cwd"]
        if item.get("timestamp") and not session.started:
            session.started = stamp(item["timestamp"])
        if not session.first_request:
            human = claude_human(item)
            if human:
                session.first_request = human
    for item in items + tail_items:
        if item.get("gitBranch"):
            session.branch = item["gitBranch"]
        if item.get("timestamp"):
            session.last = max(session.last, stamp(item["timestamp"]))
        human = claude_human(item)
        if human:
            session.last_request = human
    if full:
        for item in items:
            at = stamp(item.get("timestamp"))
            human = claude_human(item)
            if human:
                session.entries.append((at, "you", human))
            elif item.get("type") == "assistant" and not item.get("isSidechain"):
                text, tools = claude_text((item.get("message") or {}).get("content"))
                if clean(text):
                    session.entries.append((at, "claude", clean(text)))
                session.entries.extend((at, "tool", tool) for tool in tools)
    session.last = session.last or path.stat().st_mtime
    return session


# --------------------------------------------------------------------------- Codex


def read_codex(path: Path, full: bool = False) -> Session:
    session = Session("codex", path.stem.split("-", 6)[-1] if path.stem.startswith("rollout-") else path.stem, path)
    items = list(lines(path)) if full else list(lines(path, head=HEAD_BYTES))
    tail_items = [] if full else list(lines(path, tail=TAIL_BYTES))
    for item in items[:3]:
        if item.get("type") == "session_meta":
            meta = item.get("payload") or {}
            session.id = meta.get("id") or session.id
            session.cwd = meta.get("cwd", "")
            session.client = meta.get("originator", "")
            session.branch = ((meta.get("git") or {}).get("branch")) or ""
            session.started = stamp(meta.get("timestamp") or item.get("timestamp"))
            source = meta.get("source")
            session.background = session.client in BACKGROUND_CODEX or isinstance(source, dict)
    def human(item: dict[str, Any]) -> str | None:
        payload = item.get("payload") or {}
        if item.get("type") == "event_msg" and payload.get("type") == "user_message":
            return clean(payload.get("message", "")) or None
        return None
    for item in items:
        if not session.first_request and human(item):
            session.first_request = human(item)
    for item in items + tail_items:
        if item.get("timestamp"):
            session.last = max(session.last, stamp(item["timestamp"]))
        if human(item):
            session.last_request = human(item)
    if full:
        for item in items:
            at, payload = stamp(item.get("timestamp")), item.get("payload") or {}
            if human(item):
                session.entries.append((at, "you", human(item)))
            elif item.get("type") == "event_msg" and payload.get("type") == "agent_message":
                session.entries.append((at, "codex", clean(payload.get("message", ""))))
            elif item.get("type") == "response_item" and payload.get("type") in ("function_call", "custom_tool_call",
                                                                                  "local_shell_call"):
                args = payload.get("arguments") or payload.get("input") or payload.get("action") or ""
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        pass
                detail = args.get("cmd") or args.get("command") if isinstance(args, dict) else args
                if isinstance(detail, list):
                    detail = " ".join(map(str, detail))
                session.entries.append((at, "tool", f"{payload.get('name') or 'shell'}: {short(str(detail), 160)}"))
    session.last = session.last or path.stat().st_mtime
    return session


# --------------------------------------------------------------------------- finding them


def recent_files(hours: float) -> list[tuple[str, Path]]:
    cutoff = time.time() - hours * 3600
    found: list[tuple[str, Path]] = []
    if CLAUDE_DIR.is_dir():
        found += [("claude", p) for p in CLAUDE_DIR.glob("*/*.jsonl") if p.stat().st_mtime >= cutoff]
    if CODEX_DIR.is_dir():
        found += [("codex", p) for p in CODEX_DIR.glob("*/*/*/*.jsonl") if p.stat().st_mtime >= cutoff]
    return sorted(found, key=lambda pair: pair[1].stat().st_mtime, reverse=True)


def load(engine: str, path: Path, full: bool = False) -> Session:
    return read_claude(path, full) if engine == "claude" else read_codex(path, full)


def where(session: Session) -> str:
    folder = session.cwd.replace(str(HOME), "~") if session.cwd else "?"
    return folder + (f" @ {session.branch}" if session.branch else "")


def cmd_list(args: argparse.Namespace) -> int:
    shown = 0
    for engine, path in recent_files(args.hours):
        try:
            session = load(engine, path)
        except OSError:
            continue
        if session.background and not args.all:
            continue
        if args.repo and args.repo.lower() not in (session.cwd + " " + session.branch).lower():
            continue
        if not session.first_request and not args.all:
            continue  # opened and never used
        shown += 1
        print(f"{session.engine} {session.id[:8]}  {when(session.last)}  {where(session)}  ({session.client})")
        print(f"  first: {short(session.first_request)}")
        if session.last_request and session.last_request != session.first_request:
            print(f"  latest: {short(session.last_request)}")
        if shown >= args.limit:
            break
    if not shown:
        print(f"no sessions in the last {args.hours:g} hours" + (f" matching {args.repo!r}" if args.repo else ""))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    matches = [(e, p) for e, p in recent_files(args.hours) if load(e, p).id.startswith(args.id) or p.stem.endswith(args.id)
               or args.id in p.stem]
    if not matches:
        print(f"no session starting with {args.id!r} in the last {args.hours:g} hours (try --hours 720)")
        return 1
    if len(matches) > 1:
        print("several sessions match; use more of the id:")
        for engine, path in matches[:10]:
            print(f"  {engine} {load(engine, path).id}")
        return 1
    session = load(*matches[0], full=True)
    print(f"{session.engine} {session.id} · {where(session)} · {session.client} · last active {when(session.last)}")
    for at, who, text in session.entries[-args.tail:]:
        moment = datetime.fromtimestamp(at).strftime("%-m/%-d %-I:%M%p").lower() if at else ""
        body = text if who != "tool" else text
        body = clean(body, args.width) if who != "tool" else body
        print(f"\n[{moment} {who}] {body}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aakif's Claude Code and Codex sessions, read from their saved files.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    listing = sub.add_parser("list", help="recent sessions, newest activity first")
    listing.add_argument("--hours", type=float, default=72)
    listing.add_argument("--repo", help="only sessions whose folder or branch contains this")
    listing.add_argument("--limit", type=int, default=25)
    listing.add_argument("--all", action="store_true", help="include the hub's own background runs")
    show = sub.add_parser("show", help="one session as a readable transcript")
    show.add_argument("id")
    show.add_argument("--tail", type=int, default=60, help="the last N entries")
    show.add_argument("--hours", type=float, default=720)
    show.add_argument("--width", type=int, default=2000, help="cut each message at this many characters")
    args = parser.parse_args(argv)
    return cmd_list(args) if args.cmd == "list" else cmd_show(args)


if __name__ == "__main__":
    sys.exit(main())
