"""reminders: "remind me to X at Y", from Slack (`!remind`), the terminal (`agents remind`) or any Claude or
Codex session through the agents-hub MCP server. Each one is a DM from the bot when it is due, with
snooze buttons. No agent runs; a reminder costs nothing.
"""
from __future__ import annotations

import re
import secrets
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from ...core.plugin import Check, JobContext, Plugin
from ...core.store import Job, JobResult

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
UNITS = {"m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600,
         "hour": 3600, "hours": 3600, "d": 86400, "day": 86400, "days": 86400, "w": 604800, "week": 604800,
         "weeks": 604800}
CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$")
MORNING = (9, 0)  # "tomorrow" or "friday" with no time


def parse_clock(text: str) -> tuple[int, int] | None:
    match = CLOCK.match(text.strip().lower())
    if not match:
        return None
    hour, minute, half = int(match[1]), int(match[2] or 0), match[3]
    if half == "pm" and hour < 12:
        hour += 12
    if half == "am" and hour == 12:
        hour = 0
    if half is None and 1 <= hour <= 7:  # "at 5" during the day means 5pm, not 5am
        hour += 12
    return (hour, minute) if hour < 24 and minute < 60 else None


def parse_when(words: list[str], now: datetime | None = None) -> tuple[float, int] | None:
    """(epoch seconds, how many leading words it used) for a time like "in 2h", "at 3pm", "tomorrow 9am",
    "fri 10:30", "2026-11-14 09:00" or "2026-11-14T09:00"; None when the words don't start with a time."""
    now = now or datetime.now().astimezone()
    lowered = [w.lower().strip(",") for w in words]
    if not lowered:
        return None
    # in 2h / in 90 minutes / in 1 hour
    if lowered[0] == "in" and len(lowered) >= 2:
        match = re.match(r"^(\d+(?:\.\d+)?)([a-z]*)$", lowered[1])
        if match:
            unit, used = match[2], 2
            if not unit and len(lowered) >= 3:
                unit, used = lowered[2], 3
            if unit in UNITS:
                return (now + timedelta(seconds=float(match[1]) * UNITS[unit])).timestamp(), used
        return None
    # 2026-11-14 [09:00] / 2026-11-14T09:00
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}", lowered[0]):
            if "t" in lowered[0]:
                return datetime.fromisoformat(words[0]).astimezone().timestamp(), 1
            day = datetime.fromisoformat(lowered[0])
            clock = parse_clock(lowered[1]) if len(lowered) >= 2 else None
            hour, minute = clock or MORNING
            at = day.replace(hour=hour, minute=minute).astimezone()
            return at.timestamp(), 2 if clock else 1
    except ValueError:
        return None
    # [today|tomorrow|<weekday>] [at] <clock>
    index, day = 0, None
    if lowered[0] in ("today", "tonight"):
        day, index = now.date(), 1
    elif lowered[0] in ("tomorrow", "tmrw", "tmr"):
        day, index = (now + timedelta(days=1)).date(), 1
    else:
        names = [w for w in WEEKDAYS if w.startswith(lowered[0][:3])] if len(lowered[0]) >= 3 else []
        if names and lowered[0] in (names[0], names[0][:3], names[0][:4]):
            ahead = (WEEKDAYS.index(names[0]) - now.weekday()) % 7 or 7
            day, index = (now + timedelta(days=ahead)).date(), 1
    said_at = index < len(lowered) and lowered[index] == "at"
    if said_at:
        index += 1
    clock = None
    if index + 1 < len(lowered) and lowered[index + 1] in ("am", "pm"):  # "3 pm"
        clock = parse_clock(lowered[index] + lowered[index + 1])
        if clock:
            index += 1
    if clock is None and index < len(lowered):
        token = lowered[index]
        # A bare number is a time only after "at" or a day ("tomorrow 9"); "remind 3 things" is not a time.
        if said_at or day is not None or ":" in token or token.endswith(("am", "pm")):
            clock = parse_clock(token)
    if clock is None and day is None:
        return None
    hour, minute = clock or (MORNING if lowered[0] != "tonight" else (20, 0))
    used = index + (1 if clock else 0)
    target = datetime.combine(day or now.date(), datetime.min.time()).replace(hour=hour, minute=minute,
                                                                           tzinfo=now.tzinfo)
    if day is None and target <= now:  # "at 3pm" after 3pm means tomorrow
        target += timedelta(days=1)
    return target.timestamp(), used


def when_text(at: float) -> str:
    moment = datetime.fromtimestamp(at).astimezone()
    today = datetime.now().astimezone().date()
    day = "today" if moment.date() == today else "tomorrow" if moment.date() == today + timedelta(days=1) \
        else moment.strftime("%a %b %-d")
    return f"{day} {moment.strftime('%-I:%M %p').lower()}"


class Reminders(Plugin):
    name = "reminders"
    title = "Reminders"
    priority = 80

    def start(self) -> None:
        self.spawn(self.loop, "due")

    # ------------------------------------------------------------------ storing

    def add(self, text: str, at: float, source: str = "") -> str:
        rid = secrets.token_hex(3)
        self.hub.store.set_value(self.name, rid, {"text": text, "due": at, "created": time.time(), "source": source})
        return rid

    def pending(self) -> dict[str, dict[str, Any]]:
        return dict(sorted(self.hub.store.values(self.name).items(), key=lambda item: item[1].get("due", 0)))

    # ------------------------------------------------------------------ firing

    def loop(self) -> None:
        while not self.hub.stopping.is_set():
            self.fire_due()
            self.hub.stopping.wait(int(self.config.get("check_seconds", 20)))

    def fire_due(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        fired = []
        if self.hub.slack is None or not self.hub.slack.bot_dm:
            return fired
        for rid, reminder in self.pending().items():
            if reminder.get("due", 0) > now:
                break
            text = f"⏰ {reminder['text']}"
            if reminder.get("source"):
                text += f"\n_set from {reminder['source']}_"
            buttons = [self.offer("snooze", "Snooze 1h", {"text": reminder["text"], "seconds": 3600,
                                                           "source": reminder.get("source", "")}),
                       self.offer("snooze", "Tomorrow 9am", {"text": reminder["text"], "when": "tomorrow 9am",
                                                             "source": reminder.get("source", "")})]
            try:
                self.hub.slack.post_markdown(self.hub.slack.bot_dm, text,
                                             blocks=self.hub.actions.blocks(text, buttons))
            except Exception:  # noqa: BLE001 - try again on the next check rather than lose it
                self.log.exception("could not post reminder %s", rid)
                continue
            self.hub.store.delete_value(self.name, rid)
            fired.append(rid)
            self.log.info("reminded %s: %s", rid, reminder["text"][:80])
        return fired

    def act(self, name: str, args: dict[str, Any], job: Job, ctx: JobContext) -> JobResult:
        if name == "snooze":
            if args.get("when"):
                parsed = parse_when(args["when"].split())
                at = parsed[0] if parsed else time.time() + 3600
            else:
                at = time.time() + int(args.get("seconds", 3600))
            self.add(args["text"], at, args.get("source", ""))
            reply = job.payload.get("reply") or {}
            if reply.get("channel") and self.hub.slack is not None:
                self.hub.slack.post(reply["channel"], f"snoozed till {when_text(at)}", reply.get("thread_ts"))
            return JobResult(summary=f"snoozed till {when_text(at)}")
        return super().act(name, args, job, ctx)

    def handle(self, job: Job, ctx: JobContext) -> JobResult:
        return JobResult(summary="reminders fire on their own; nothing to run")

    # ------------------------------------------------------------------ commands

    def cmd_remind(self, args: list[str]) -> str:
        source = ""
        if "--from" in args:
            i = args.index("--from")
            source, args = " ".join(args[i + 1:i + 2]), args[:i] + args[i + 2:]
        if args[:1] == ["--when"] and len(args) >= 3:
            parsed = parse_when(args[1].split())
            if not parsed or parsed[1] != len(args[1].split()):
                return f"couldn't read {args[1]!r} as a time (try: in 2h, at 3pm, tomorrow 9am, fri 10:30, 2026-11-14 09:00)"
            at, text = parsed[0], " ".join(args[2:])
        else:
            words = [w for w in args if w != "me" or args.index(w) > 0]
            parsed = parse_when(words)
            if not parsed:
                return "start with when: `!remind in 2h check the deploy`, `!remind tomorrow 9am ...`"
            at, text = parsed[0], " ".join(words[parsed[1]:])
        text = re.sub(r"^(to|that)\s+", "", text.strip(), flags=re.I)
        if not text:
            return "remind you of what?"
        if at <= time.time():
            return f"{when_text(at)} has already passed"
        rid = self.add(text, at, source)
        return f"⏰ {when_text(at)}: {text} (`{rid}`)"

    def cmd_list(self, _: list[str]) -> str:
        items = self.pending()
        if not items:
            return "no reminders pending"
        return "\n".join(f"`{rid}` {when_text(r['due'])}: {r['text']}" for rid, r in items.items())

    def cmd_drop(self, args: list[str]) -> str:
        if not args:
            return "which one? `!unremind <id>`"
        matches = [rid for rid in self.pending() if rid.startswith(args[0])]
        if len(matches) != 1:
            return f"no single reminder matches {args[0]}"
        self.hub.store.delete_value(self.name, matches[0])
        return f"dropped `{matches[0]}`"

    def aliases(self) -> dict[str, Callable[[list[str]], str]]:
        return {"remind": self.cmd_remind, "reminders": self.cmd_list, "unremind": self.cmd_drop}

    def status(self) -> dict[str, Any]:
        items = self.pending()
        nxt = next(iter(items.values()), None)
        return {"pending": len(items), "next": f"{when_text(nxt['due'])}: {nxt['text'][:60]}" if nxt else None}

    def checks(self) -> list[Check]:
        return [Check("parses times", parse_when(["in", "5m"]) is not None)]
