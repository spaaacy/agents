"""Turning an agent's event stream into short readable lines, for the dashboard and `agents watch`.

Both engines write one JSON event per line to a job's events.jsonl: Claude's
stream-json and Codex's --json. render() keeps what a person watching cares
about (commands, tool calls, the agent's messages, refusals, totals) and leaves
out thinking and raw tool output, which stay in the file.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

CODEX_EVENTS = {"thread.started", "turn.started", "turn.completed", "turn.failed", "error", "item.started", "item.completed"}
TEXT_LIMIT = 700


def clean(value: object) -> str:
    """Text safe to print: no terminal control sequences from Slack, tools or the model."""
    value = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", str(value))
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return "".join(c for c in value if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) < 160))


def short(text: object, limit: int = TEXT_LIMIT) -> str:
    text = " ".join(str(text).split()) if limit < 200 else str(text).strip()
    return text if len(text) <= limit else text[:limit] + " …"


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


def render_codex(event: dict[str, Any]) -> list[str]:
    kind = event.get("type")
    item = event.get("item") if isinstance(event.get("item"), dict) else {}
    item_kind = item.get("type")
    if kind == "thread.started":
        return [f"Codex session {event.get('thread_id', '?')}"]
    if kind == "item.started" and item_kind == "command_execution":
        return [f"Running: {short(item.get('command', ''), 300)}"]
    if kind == "item.started" and item_kind == "mcp_tool_call":
        args = short(json.dumps(item.get("arguments") or {}), 160)
        return [f"Calling: {item.get('server', '?')}.{item.get('tool', '?')} {args}"]
    if kind == "item.completed" and item_kind == "agent_message" and str(item.get("text", "")).strip():
        return [f"Codex: {short(item['text'])}"]
    if kind == "item.completed" and item_kind == "command_execution":
        if item.get("status") == "completed" and item.get("exit_code") in (0, None):
            return []
        output = next((line for line in str(item.get("aggregated_output") or "").splitlines() if line.strip()), "")
        exit_code = item.get("exit_code")
        return [f"Tool refused or failed: {short(item.get('command', ''), 120)}"
                + (f" (exit {exit_code})" if exit_code is not None else "")
                + (f": {short(output, 160)}" if output else "")]
    if kind == "item.completed" and item_kind == "mcp_tool_call" and item.get("error"):
        error = item["error"].get("message") if isinstance(item["error"], dict) else item["error"]
        return [f"Tool refused or failed: {item.get('server', '?')}.{item.get('tool', '?')}: {short(error, 200)}"]
    if kind == "turn.completed":
        usage = event.get("usage") or {}
        tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        return [f"Codex done, {tokens:,} tokens"]
    if kind in ("turn.failed", "error"):
        error = event.get("error") if isinstance(event.get("error"), dict) else event
        return [f"Codex failed: {short(error.get('message') or error, 300)}"]
    return []


def render(event: dict[str, Any]) -> list[str]:
    """The lines one event is worth showing, for either engine; [] for anything not worth a line."""
    if not isinstance(event, dict):
        return []
    kind = event.get("type")
    if kind in CODEX_EVENTS:
        return render_codex(event)
    if kind == "system" and event.get("subtype") == "init":
        return [f"Claude session {event.get('session_id', '?')}"]
    if kind == "assistant":
        lines = []
        for part in (event.get("message") or {}).get("content", []) or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and str(part.get("text", "")).strip():
                lines.append(f"Claude: {short(part['text'])}")
            elif part.get("type") == "tool_use":
                lines.append(describe_tool(str(part.get("name", "?")), part.get("input") or {}))
        return lines
    if kind == "user":
        lines = []
        for part in (event.get("message") or {}).get("content", []) or []:
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
        return [f"Claude done in {seconds:.0f}s" + (f", ${cost:.2f}" if cost is not None else "")
                + (f", {refused} tool calls refused" if refused else "")]
    return []


def render_line(line: str) -> list[str]:
    try:
        event = json.loads(line)
    except ValueError:
        return []
    return [clean(text) for text in render(event)]


def read_events(path: Path, offset: int = 0) -> tuple[list[str], int]:
    """Rendered lines from the complete events added to ``path`` since byte ``offset``, and the new offset.

    A line still being written (no newline yet) is left for the next read.
    """
    try:
        with path.open("rb") as source:
            source.seek(0, 2)
            size = source.tell()
            if offset > size:  # the file was replaced: start over
                offset = 0
            source.seek(offset)
            data = source.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n")
    if end == -1:
        return [], offset
    lines: list[str] = []
    for raw in data[: end + 1].splitlines():
        lines.extend(render_line(raw.decode("utf-8", errors="replace")))
    return lines, offset + end + 1


def tail_events(path: Path, limit: int = 400) -> tuple[list[str], int]:
    """The last ``limit`` rendered lines of a run, and the offset to tail from."""
    lines, offset = read_events(path, 0)
    return lines[-limit:], offset
