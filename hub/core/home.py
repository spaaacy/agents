"""The app's Home tab in Slack: the hub's status, its controls, and the `!` commands, for the owner only.

Opening the app in Slack (the Home tab) draws it fresh from the hub. Its
buttons and menus run the same control commands as the dashboard and the `!`
messages: switch engine, pick model and effort, pause or resume a plugin,
retry a failed job. Anyone but the owner sees a one-line note instead.

Needs the manifest's Home tab on ("home_tab_enabled": true), interactivity on,
and the "app_home_opened" bot event.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from . import engine
from .controls import HELP

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger("hub.home")
STATUS_ICONS = {"queued": "⏳", "running": "🔄", "done": "✅", "failed": "❌", "cancelled": "⛔"}
MAX_JOBS = 8


def escape(text: Any) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def ago(at: float | None) -> str:
    if not at:
        return ""
    seconds = int(time.time() - at)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return f"{seconds}s ago"


def option(value: str, label: str) -> dict[str, Any]:
    return {"text": {"type": "plain_text", "text": label[:75]}, "value": value[:150]}


def section(text: str, accessory: dict[str, Any] | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}}
    if accessory:
        block["accessory"] = accessory
    return block


def button(text: str, action_id: str, *, style: str | None = None, value: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"type": "button", "text": {"type": "plain_text", "text": text}, "action_id": action_id,
                           "value": value or action_id}
    if style:
        out["style"] = style
    return out


def engine_blocks(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """The engine, model and effort pickers: every plugin's setting, then the PR reviews' own."""
    blocks = picker_blocks(snapshot, snapshot["engine"], snapshot.get("engine_text"), "all")
    if snapshot.get("review_engine"):
        blocks += picker_blocks(snapshot, snapshot["review_engine"], snapshot.get("review_engine_text"), "reviews")
    return blocks


def picker_blocks(snapshot: dict[str, Any], choice: dict[str, Any], text: str | None, scope: str) -> list[dict[str, Any]]:
    # Action ids carry the scope after a dot ("model.reviews"), so the reviews row switches only the reviews setting.
    suffix = "" if scope == "all" else f".{scope}"
    name = choice["engine"]
    pick = choice.get(name, {})
    models = snapshot.get("engine_options", {}).get(name, [])
    model_options = [option(m["slug"], m["label"] if m["label"] == m["slug"] else f"{m['label']} ({m['slug']})")
                     for m in models][:100]
    info = next((m for m in models if m["slug"] == pick.get("model")), None)
    efforts = info["efforts"] if info else ["low", "medium", "high", "xhigh", "max"]
    default_label = "default effort" + (f" ({info['default_effort']})" if info and info.get("default_effort") else "")
    effort_options = [option("default", default_label)] + [option(e, f"{e} effort") for e in efforts]
    model_select: dict[str, Any] = {"type": "static_select", "action_id": f"model{suffix}",
                                    "placeholder": {"type": "plain_text", "text": "default model"},
                                    "options": model_options}
    current_model = next((o for o in model_options if o["value"] == pick.get("model")), None)
    if current_model:
        model_select["initial_option"] = current_model
    effort_select = {"type": "static_select", "action_id": f"effort{suffix}", "options": effort_options,
                     "initial_option": next((o for o in effort_options if o["value"] == (pick.get("effort") or "default")),
                                            effort_options[0])}
    title = "*Agents run on*" if scope == "all" else "*PR reviews run on*"
    return [
        section(f"{title} {escape(text or name)}"),
        {"type": "actions", "elements": [
            button("Codex", f"engine{suffix}:codex", style="primary" if name == "codex" else None),
            button("Claude", f"engine{suffix}:claude", style="primary" if name == "claude" else None),
            model_select, effort_select]},
    ]


def plugin_blocks(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = []
    counts = snapshot.get("counts", {})
    for name, plugin in snapshot.get("plugins", {}).items():
        c = counts.get(name, {})
        state = "⏸ paused" if plugin.get("paused") else "on"
        line = (f"*{escape(plugin.get('title') or name)}* (`{name}`) · {state} · {c.get('running', 0)} running · "
                f"{c.get('queued', 0)} queued · {c.get('failed', 0)} failed this week")
        action = button("Resume", f"resume:{name}", style="primary") if plugin.get("paused") \
            else button("Pause", f"pause:{name}")
        blocks.append(section(line, action))
    for name, why in (snapshot.get("broken") or {}).items():
        blocks.append(section(f"*{escape(name)}* didn't load: {escape(why[:300])}"))
    return blocks


def job_blocks(jobs: list[Any]) -> list[dict[str, Any]]:
    if not jobs:
        return [section("_No jobs yet._")]
    blocks = []
    for job in jobs[:MAX_JOBS]:
        title = escape(job.title[:120])
        if job.link:
            title = f"<{job.link}|{title}>"
        detail = " · ".join(bit for bit in (job.plugin, ago(job.created), job.engine or "",
                                              escape(job.error[:150]) if job.error and job.status == "failed" else "") if bit)
        text = f"{STATUS_ICONS.get(job.status, '•')} {title}\n`{job.id}` · {detail}"
        accessory = button("Retry", f"retry:{job.id}") if job.status in ("failed", "cancelled") else None
        blocks.append(section(text, accessory))
    return blocks


def build_view(hub: Hub, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = snapshot or hub.snapshot()
    name = (hub.slack.bot_name if hub.slack else None) or "Agents"
    title = next((alias for alias in getattr(hub.slack, "bot_aliases", []) if " " in alias or "'" in alias), name)
    prefix = hub.cfg.get("slack", {}).get("control_prefix", "!")
    commands = "\n".join(prefix + line[1:] if line.startswith("!") else line for line in HELP.splitlines())
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text":
            f"{len(snapshot.get('running', []))} of {snapshot.get('slots', '?')} slots busy · "
            f"updated {time.strftime('%-I:%M %p')} · dashboard on the Mac: http://127.0.0.1:8765"}]},
        *engine_blocks(snapshot),
        {"type": "divider"},
        *plugin_blocks(snapshot),
        {"type": "divider"},
        section("*Recent jobs*", button("Refresh", "refresh")),
        *job_blocks(hub.store.jobs(limit=MAX_JOBS)),
        {"type": "divider"},
        section(f"*Commands* (send them to me in this app's Messages tab)\n```{commands}```"),
    ]
    return {"type": "home", "blocks": blocks[:100]}


def action_words(action: dict[str, Any]) -> list[str] | None:
    """The control command a Home tab button or menu stands for; None for refresh or anything unknown."""
    action_id = str(action.get("action_id", ""))
    kind, _, arg = action_id.partition(":")
    kind, _, scope = kind.partition(".")
    scoped = [scope] if scope in engine.SCOPES and scope != "all" else []
    if kind in ("model", "effort") and not arg:
        value = (action.get("selected_option") or {}).get("value")
        return [kind, *scoped, value] if value else None
    if kind == "engine" and arg:
        return [kind, *scoped, arg]
    if kind in ("pause", "resume", "retry") and arg and not scope:
        return [kind, arg]
    return None


class HomeTab:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    def start(self) -> None:
        if self.hub.slack is not None:
            self.hub.slack.subscribe(self.on_event)
            self.hub.slack.subscribe_interactive(self.on_action)

    def publish(self, user: str) -> None:
        if user != self.hub.owner:
            view = {"type": "home", "blocks": [section("This app runs on Aakif's laptop and answers only him.")]}
        else:
            view = build_view(self.hub)
        self.hub.slack.web.views_publish(user_id=user, view=view)

    def on_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "app_home_opened" and event.get("tab", "home") == "home":
            self.publish(event["user"])

    def on_action(self, payload: dict[str, Any]) -> None:
        if payload.get("type") != "block_actions" or (payload.get("view") or {}).get("type") != "home":
            return
        user = (payload.get("user") or {}).get("id")
        if user != self.hub.owner:
            return
        for action in payload.get("actions", []):
            words = action_words(action)
            if words:
                log.info("home tab: %s", " ".join(words))
                self.hub.command(words)
        self.publish(user)
