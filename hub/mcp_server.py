"""agents-hub: an MCP server (stdio) that lets Aakif's own Claude and Codex sessions talk to his agents hub.

Tools: message him on Slack now, set a reminder, hand the hub a background task (its answer lands in his
Slack DM), check on the hub or a job, and read or add to his on-call notebook (hub/core/oncall_notes.py, read
and written directly, so it works with the hub stopped). Each call goes to the running hub through its local API
(127.0.0.1, the dashboard token); with the hub stopped, the tools say so.

Registered for both CLIs from ~/.agents/mcp/servers.json ("agents-hub"); run by hand with
`PYTHONPATH=~/.agents ~/.agents/hub/.venv/bin/python -m hub.mcp_server`.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

from hub.core import oncall_notes

STATE_DIR = Path.home() / ".agents" / "state" / "hub"
CONFIG = Path(__file__).resolve().parent / "config.json"

TOOLS = [
    {
        "name": "slack_me",
        "description": "Send Aakif a Slack DM right now from his bot (Aakif's Dot). Use it when he asks to be "
                       "told something on Slack or pinged when you finish, never on your own initiative.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description": "the message, Slack markdown"}},
                        "required": ["text"]},
    },
    {
        "name": "remind_me",
        "description": "Set a reminder: his bot DMs him at that time, with snooze buttons. Use when he says "
                       "'remind me to X'. 'when' is like: in 2h, in 30 minutes, at 3pm, tomorrow 9am, fri 10:30, "
                       "2026-11-14 09:00 (his local time).",
        "inputSchema": {"type": "object", "properties": {
            "when": {"type": "string", "description": "when to remind him"},
            "text": {"type": "string", "description": "what to remind him of, phrased as he'd want to read it"}},
            "required": ["when", "text"]},
    },
    {
        "name": "run_in_background",
        "description": "Hand a task to his agents hub to do in the background while this session carries on; "
                       "the answer lands in his Slack DM. It runs read-only (reads code, GitHub, Linear, Datadog, "
                       "the read-only database). Set allow_changes only when he asked for a task that edits code "
                       "or tickets: then it asks him for an OK in Slack and does nothing until he taps it. "
                       "Write the task so it stands alone (the background agent can't see this conversation).",
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string", "description": "the full task, with every detail it needs"},
            "allow_changes": {"type": "boolean", "description": "may edit code or tickets after his OK", "default": False}},
            "required": ["task"]},
    },
    {
        "name": "oncall_notes",
        "description": "Aakif's on-call memory, shared with his agents hub (Aakif's Dot): what each Datadog monitor "
                       "usually turns out to be, what to check first, what was ruled out and what is still open, then "
                       "any notebook entries newer than it. Read it before investigating a page or answering about an "
                       "alert or incident. With monitors: their newer entries come first. With query: every notebook "
                       "entry holding all the words.",
        "inputSchema": {"type": "object", "properties": {
            "monitors": {"type": "array", "items": {"type": "string"}, "description": "Datadog monitor ids"},
            "query": {"type": "string", "description": "words to search every entry for"}}},
    },
    {
        "name": "oncall_note",
        "description": "Add to Aakif's on-call notebook. Save an on-call investigation's findings when it finishes "
                       "(what fired, what it was, evidence, what was ruled out, what was done or is next), and "
                       "anything he asks to remember for the shift. Counts and ids only, never customer message text.",
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string", "description": "the findings, standalone (whoever reads it can't see this session)"},
            "monitors": {"type": "array", "items": {"type": "string"},
                         "description": "the monitors it is about, each \"<id> <name>\""},
            "investigation": {"type": "boolean", "default": False,
                              "description": "true when this is an investigation's findings, false for a note"}},
            "required": ["text"]},
    },
    {
        "name": "hub_status",
        "description": "What his agents hub is doing: the engine, each agent's running, queued and failed jobs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "hub_job",
        "description": "One hub job by id (or the start of one): its state, result line, link and resume command.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
]


def hub_command(words: list[str]) -> str:
    """Run a control command in the live hub; a plain sentence when it can't be reached."""
    try:
        cfg = json.loads(CONFIG.read_text()).get("dashboard", {})
        url = f"http://{cfg.get('host', '127.0.0.1')}:{cfg.get('port', 8765)}/api/command"
        token = (STATE_DIR / "dashboard.token").read_text().strip()
        request = urllib.request.Request(url, data=json.dumps({"words": words}).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-Hub-Token": token})
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())["reply"]
    except Exception as exc:  # noqa: BLE001 - reported to the caller as text
        return f"the agents hub isn't reachable ({type(exc).__name__}); is it running? (`agents status`)"


class Server:
    def __init__(self) -> None:
        self.client = "an agent session"

    def source(self) -> str:
        where = os.getcwd().replace(str(Path.home()), "~")
        return f"{self.client} in {where}"

    def call(self, name: str, args: dict[str, Any]) -> str:
        if name == "slack_me":
            return hub_command(["notify", str(args.get("text", "")).strip()])
        if name == "remind_me":
            return hub_command(["remind", "--when", str(args.get("when", "")), str(args.get("text", "")),
                                "--from", self.source()])
        if name == "run_in_background":
            words = ["bg", "--cwd", os.getcwd(), "--from", self.source()]
            if args.get("allow_changes"):
                words.append("--changes")
            return hub_command(words + [str(args.get("task", ""))])
        if name == "oncall_notes":
            query = str(args.get("query") or "").strip()
            if query:
                return oncall_notes.command(["search", *query.split()], source=self.source())
            return oncall_notes.digest(monitors=[str(m) for m in args.get("monitors") or []])
        if name == "oncall_note":
            entry = oncall_notes.add("investigation" if args.get("investigation") else "note", str(args.get("text", "")),
                                     source=self.source(), monitors=[str(m) for m in args.get("monitors") or []])
            return f"saved to the on-call notebook ({entry['id']}); the hub's next investigation reads it"
        if name == "hub_status":
            return hub_command(["status"])
        if name == "hub_job":
            return hub_command(["job", str(args.get("id", ""))])
        raise KeyError(name)

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method, mid = message.get("method"), message.get("id")
        if mid is None:  # a notification (initialized, cancelled): nothing to answer
            return None
        if method == "initialize":
            info = (message.get("params") or {}).get("clientInfo") or {}
            self.client = info.get("name") or self.client
            version = (message.get("params") or {}).get("protocolVersion") or "2025-06-18"
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": version, "capabilities": {"tools": {}},
                "serverInfo": {"name": "agents-hub", "version": "1.0"},
                "instructions": "Aakif's agents hub on this Mac: Slack him, set reminders, run tasks in the background, "
                                "and read or add to his on-call notebook."}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
        if method == "tools/call":
            params = message.get("params") or {}
            try:
                text = self.call(params.get("name", ""), params.get("arguments") or {})
            except KeyError:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {params.get('name')}"}}
            except ValueError as exc:  # e.g. an empty note
                return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": str(exc)}],
                                                                 "isError": True}}
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}],
                                                             "isError": text.startswith("the agents hub isn't reachable")}}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}

    def serve(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            reply = self.handle(message)
            if reply is not None:
                sys.stdout.write(json.dumps(reply) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    Server().serve()
