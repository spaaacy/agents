"""Approve-to-act buttons: a button on a Slack message that, when the owner taps it, runs one action.

A plugin offers an action with ``plugin.offer(name, label, args)``, which
stores it under a random token and returns a Block Kit button; the plugin puts
the button in the message it posts (``hub.actions.blocks(text, buttons)``).
Tapping it (owner only, once, within a week) queues a job on that plugin with
payload {"action": name, "args": args, "reply": {...}}; the scheduler hands it
to ``plugin.act()``. The message is updated in place: the tapped button
becomes "▶ <label>: started <job>", the rest stay.

Two kinds of action:
- fixed ones a plugin codes itself (post a drafted review), and
- "agent": run an agent in the hub's act mode (agent.py) on an instruction,
  resuming the session that proposed it when there is one. Plugin.act() does
  this one for every plugin; a plugin only offers the button.
"""
from __future__ import annotations

import logging
import secrets
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger("hub.actions")
SCOPE = "actions"
TTL_SECONDS = 7 * 86400
SECTION_CHARS = 2900


def text_sections(text: str) -> list[dict[str, Any]]:
    """Long mrkdwn split into section blocks (3000 characters each at most), at line breaks where possible."""
    blocks, rest = [], text.strip() or " "
    while rest:
        if len(rest) <= SECTION_CHARS:
            cut = len(rest)
        else:
            cut = rest.rfind("\n", 0, SECTION_CHARS)
            cut = cut if cut > 0 else SECTION_CHARS
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": rest[:cut]}})
        rest = rest[cut:].lstrip("\n")
    return blocks[:45]


class Actions:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    def start(self) -> None:
        if self.hub.slack is not None:
            self.hub.slack.subscribe_interactive(self.on_action)

    # ------------------------------------------------------------------ offering

    def button(self, plugin: str, name: str, label: str, args: dict[str, Any] | None = None, *,
               style: str | None = None, confirm: str | None = None) -> dict[str, Any]:
        token = secrets.token_hex(8)
        self.hub.store.set_value(SCOPE, token, {"plugin": plugin, "name": name, "label": label, "args": args or {},
                                                "created": time.time(), "used": None})
        element: dict[str, Any] = {"type": "button", "text": {"type": "plain_text", "text": label[:75]},
                                   "action_id": f"act:{token}", "value": token}
        if style:
            element["style"] = style
        if confirm:  # Slack's own "are you sure?" dialog before the tap counts
            element["confirm"] = {"title": {"type": "plain_text", "text": "Go ahead?"},
                                  "text": {"type": "mrkdwn", "text": confirm[:300]},
                                  "confirm": {"type": "plain_text", "text": "Do it"},
                                  "deny": {"type": "plain_text", "text": "Cancel"}}
        return element

    @staticmethod
    def blocks(text: str, buttons: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = text_sections(text)
        if buttons:
            out.append({"type": "actions", "elements": buttons[:25]})
        return out

    # ------------------------------------------------------------------ tapping

    def on_action(self, payload: dict[str, Any]) -> None:
        if payload.get("type") != "block_actions" or (payload.get("container") or {}).get("type") != "message":
            return
        user = (payload.get("user") or {}).get("id")
        for action in payload.get("actions", []):
            if not str(action.get("action_id", "")).startswith("act:"):
                continue
            if user != self.hub.owner:
                return
            self.run(action.get("value", ""), payload)

    def run(self, token: str, payload: dict[str, Any]) -> str | None:
        """Queue the action behind ``token``; returns the job id, or None when the button no longer works."""
        record = self.hub.store.get_value(SCOPE, token)
        channel = (payload.get("channel") or {}).get("id") or (payload.get("container") or {}).get("channel_id")
        message = payload.get("message") or {}
        thread_ts = message.get("thread_ts") or message.get("ts")
        problem = None
        if not record:
            problem = "That button doesn't work any more (the hub no longer knows it)."
        elif record.get("used"):
            problem = f"Already done: that button started `{record['used']}`."
        elif time.time() - record.get("created", 0) > TTL_SECONDS:
            problem = "That button expired (they last a week). Ask again and I'll offer a fresh one."
        elif record["plugin"] not in self.hub.plugins:
            problem = f"The {record['plugin']} plugin isn't running."
        if problem:
            if channel and self.hub.slack is not None:
                self.hub.slack.post(channel, problem, thread_ts)
            return None
        plugin = self.hub.plugins[record["plugin"]]
        job = plugin.enqueue(record["label"], {"action": record["name"], "args": record["args"],
                                               "reply": {"channel": channel, "thread_ts": thread_ts}},
                             dedupe=f"action:{token}", priority=plugin.priority + 5)
        if job is None:
            return None
        record["used"] = job.id
        self.hub.store.set_value(SCOPE, token, record)
        log.info("action %s (%s) approved: job %s", record["name"], record["label"], job.id)
        self._mark_started(payload, token, f"▶ {record['label']}: started `{job.id}`")
        return job.id

    def _mark_started(self, payload: dict[str, Any], token: str, note: str) -> None:
        """Swap the tapped button for a note on the original message; the other buttons stay."""
        if self.hub.slack is None:
            return
        message = payload.get("message") or {}
        channel = (payload.get("channel") or {}).get("id")
        blocks = message.get("blocks") or []
        if not channel or not message.get("ts") or not blocks:
            return
        new_blocks = []
        for block in blocks:
            if block.get("type") == "actions":
                kept = [e for e in block.get("elements", []) if e.get("value") != token]
                if kept:
                    new_blocks.append({**block, "elements": kept})
            else:
                new_blocks.append(block)
        new_blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": note}]})
        try:
            self.hub.slack.web.chat_update(channel=channel, ts=message["ts"], blocks=new_blocks,
                                           text=message.get("text") or note)
        except Exception:  # noqa: BLE001 - the job runs either way; the note is cosmetic
            log.warning("could not mark the button as used", exc_info=True)
