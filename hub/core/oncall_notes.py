#!/usr/bin/env python3
"""Aakif's on-call notebook: what each page turned out to be, shared by every agent session on this Mac.

One append-only file kept forever, ~/.agents/state/on-call/notes.jsonl (not in git), one entry per line:
  shift_start, shift_end  the hub's on-call watcher marks his shifts as Datadog On-Call reports them
  investigation           an on-call investigation's findings: the hub's page jobs and local /on-call runs
  note                    anything he or a session saves on purpose: what was ruled out, what was done
notebook.md beside it is the same entries rendered for reading: the current shift first, then the rest.

memory.md is what the entries add up to: one short document by monitor (what it usually turns out to be, how often
it paged, what to check first, what was ruled out) plus what is still open. The hub rewrites it with an agent after
new entries land (memory_prompt / save_memory; memory.json says which entry it reaches), and digest() hands it to
every reader, followed by any entries newer than it.

Who writes: the hub (shift marks, every page investigation), local sessions through the agents-hub MCP tool
`oncall_note` (the /on-call skill saves its findings that way), `agents oncall note ...`, and `!oncall note ...`
in Slack. Who reads: the hub's page investigations (in their prompt), Slack answers and local sessions (this
script, or the MCP tool `oncall_notes`).

  python3 ~/.agents/hub/core/oncall_notes.py show [--monitor ID]...   this shift, then earlier pages of those monitors
  python3 ~/.agents/hub/core/oncall_notes.py search <words>            every entry holding all the words, newest first
  python3 ~/.agents/hub/core/oncall_notes.py note <text> [--monitor "ID name"]...

Stdlib only, so any python3 runs it. Never write customer message or email text here: counts and ids only.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import secrets
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KINDS = ("shift_start", "shift_end", "investigation", "note")
MAX_TEXT = 6000            # one entry's text, stored
RENDERED_ENTRIES = 300     # notebook.md shows this many earlier entries; the file keeps all of them
PACIFIC = ZoneInfo("America/Los_Angeles")


def folder() -> Path:
    """Where the notebook lives; ONCALL_NOTES_DIR moves it (tests)."""
    return Path(os.environ.get("ONCALL_NOTES_DIR") or Path.home() / ".agents" / "state" / "on-call")


def notes_file() -> Path:
    return folder() / "notes.jsonl"


def rendered_file() -> Path:
    return folder() / "notebook.md"


def memory_file() -> Path:
    return folder() / "memory.md"


def memory_state_file() -> Path:
    return folder() / "memory.json"


# --------------------------------------------------------------------------- reading


def entries() -> list[dict[str, Any]]:
    """Every entry, oldest first; a line that doesn't parse is skipped."""
    try:
        lines = notes_file().read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("kind") in KINDS and entry.get("at"):
            out.append(entry)
    return sorted(out, key=lambda e: e["at"])


def when(entry: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(entry["at"]))


def shift(items: Sequence[dict[str, Any]]) -> tuple[datetime | None, datetime | None]:
    """The latest shift as (start, end): end is None while it is still on. (None, None) before any shift.

    A shift runs from the first start mark after the previous end mark, so a later start mark with an earlier
    time (the real start read from Datadog's schedule) moves the start back."""
    current: dict[str, Any] | None = None
    last: tuple[datetime | None, datetime | None] = (None, None)
    for mark in (e for e in items if e["kind"] in ("shift_start", "shift_end")):  # time order, file order within a second
        if mark["kind"] == "shift_start":
            current = current or mark
        elif current is not None:
            last, current = (when(current), when(mark)), None
    return (when(current), None) if current is not None else last


def on_call(items: Sequence[dict[str, Any]] | None = None) -> bool:
    start, end = shift(entries() if items is None else items)
    return start is not None and end is None


def monitor_ids(monitors: Iterable[str]) -> set[str]:
    """The ids in "123 name" monitor labels (an id alone works too)."""
    return {m.split()[0] for m in monitors if str(m).strip() and str(m).split()[0].isdigit()}


def search(words: str | Sequence[str], items: Sequence[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Entries holding every word (in their text, monitors or source), newest first."""
    terms = [w.lower() for w in (words.split() if isinstance(words, str) else words) if w.strip()]
    found = []
    for entry in reversed(entries() if items is None else items):
        if entry["kind"] not in ("investigation", "note"):
            continue
        hay = " ".join([entry.get("text", ""), " ".join(entry.get("monitors", [])), entry.get("source", "")]).lower()
        if all(term in hay for term in terms):
            found.append(entry)
    return found


def pacific(at: datetime) -> str:
    local = at.astimezone(PACIFIC)
    return local.strftime("%a %b %-d %-I:%M%p").replace("AM", "am").replace("PM", "pm") + " PT"


def fmt(entry: dict[str, Any], chars: int = 1500) -> str:
    text = str(entry.get("text", "")).strip()
    if len(text) > chars:
        text = text[:chars].rstrip() + " …"
    head = f"- {pacific(when(entry))} · {entry['kind']} · {entry.get('source', '?')}"
    if entry.get("monitors"):
        head += " · " + ", ".join(entry["monitors"])
    lines = [head]
    if text:
        lines += ["  " + line for line in text.splitlines()]
    if entry.get("link"):
        lines.append(f"  link: {entry['link']}")
    if entry.get("session"):
        lines.append(f"  session: {entry['session']}")
    return "\n".join(lines)


def first_line(entry: dict[str, Any]) -> str:
    return next((line.strip() for line in str(entry.get("text", "")).splitlines() if line.strip()), "")


def brief(entry: dict[str, Any]) -> str:
    mons = ", ".join(entry.get("monitors", []))
    return f"- {pacific(when(entry))}{' · ' + mons if mons else ''}: {first_line(entry)[:160]}"


def log_digest(monitors: Iterable[str] = (), limit: int = 8000, items: Sequence[dict[str, Any]] | None = None) -> str:
    """The entries themselves, for when there is no memory yet. This shift: the entries about the given monitors in full, then the
    newest others in full and the rest one line each. Then earlier shifts: entries about those monitors, then the
    most recent other findings in one line each."""
    items = entries() if items is None else list(items)
    start, end = shift(items)
    wanted = monitor_ids(monitors)

    def about(entry: dict[str, Any]) -> bool:
        return bool(wanted and monitor_ids(entry.get("monitors", [])) & wanted)

    parts: list[str] = []
    in_shift: list[dict[str, Any]] = []
    if start is None:
        parts.append("No shift recorded yet.")
    else:
        label = (f"This shift (on call since {pacific(start)})" if end is None
                 else f"Last shift ({pacific(start)} to {pacific(end)}; not on call now)")
        in_shift = [e for e in items if e["kind"] in ("investigation", "note") and when(e) >= start
                    and (end is None or when(e) <= end)]
        newest = list(reversed(in_shift))
        same = [e for e in newest if about(e)][:6]
        others = [e for e in newest if not about(e)]
        full = others[:2 if same else 6]
        parts.append(f"{label}, {len(in_shift)} entr{'y' if len(in_shift) == 1 else 'ies'}:")
        if not in_shift:
            parts.append("- nothing written yet this shift")
        if same:
            parts.append("About these monitors:")
            parts += [fmt(e) for e in same]
            if others:
                parts.append("Newest others:")
        parts += [fmt(e) for e in full]
        rest = [e for e in newest if e not in same and e not in full][:25]
        if rest:
            parts.append("Earlier this shift:")
            parts += [brief(e) for e in rest]
    earlier = [e for e in reversed(items) if e["kind"] in ("investigation", "note") and e not in in_shift]
    same_before = [e for e in earlier if about(e)][:5]
    if same_before:
        parts.append("\nEarlier shifts, about these monitors:")
        parts += [fmt(e, 800) for e in same_before]
    recent = [e for e in earlier if not about(e)][:10]
    if recent:
        parts.append("\nEarlier shifts, other recent findings (search for more):")
        parts += [brief(e) for e in recent]
    text = "\n".join(parts)
    return text if len(text) <= limit else text[:limit].rstrip() + "\n… (cut; `search` finds the rest)"


# --------------------------------------------------------------------------- the memory

MEMORY_BATCH = 15          # entries folded in per rewrite (the first build takes several)
MEMORY_TITLE = "# On-call memory"


def memory_state() -> dict[str, Any]:
    try:
        state = json.loads(memory_state_file().read_text())
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def read_memory() -> str | None:
    try:
        text = memory_file().read_text().strip()
    except OSError:
        return None
    return text or None


def unconsolidated(items: Sequence[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Investigations and notes the memory does not take in yet, oldest first."""
    items = entries() if items is None else list(items)
    findings = [e for e in items if e["kind"] in ("investigation", "note")]
    through = memory_state().get("through")
    ids = [e["id"] for e in findings]
    if read_memory() is None or not through or through not in ids:
        return findings
    return findings[ids.index(through) + 1:]


def monitor_stats(items: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per monitor id: its latest label, how many entries name it, and the first and last of them (PT dates)."""
    stats: dict[str, dict[str, Any]] = {}
    for e in items:
        if e["kind"] not in ("investigation", "note"):
            continue
        for label in e.get("monitors", []):
            ids = monitor_ids([label])
            if not ids:
                continue
            mid = next(iter(ids))
            s = stats.setdefault(mid, {"label": label, "count": 0, "first": e["at"], "last": e["at"]})
            s["label"], s["count"], s["last"] = label, s["count"] + 1, e["at"]
    for s in stats.values():
        s["first"], s["last"] = pacific(datetime.fromisoformat(s["first"])), pacific(datetime.fromisoformat(s["last"]))
    return stats


def memory_prompt(current: str | None, new: Sequence[dict[str, Any]], stats: dict[str, dict[str, Any]],
                  shift_line: str) -> str:
    """The rewrite request: the memory as it stands, the counts, the entries to fold in."""
    counts = "\n".join(f"- {s['label']}: in {s['count']} entries, {s['first']} to {s['last']}"
                        for s in sorted(stats.values(), key=lambda s: s["last"], reverse=True)) or "- none yet"
    return f"""You keep Aakif's on-call memory: one short document that every on-call investigation reads before it \
starts (his own Claude and Codex sessions and his agents hub), so it can build on what is already known instead of \
starting from zero. Rewrite it to take in the new notebook entries below. Answer with only the new document, in \
Markdown, starting with the line "{MEMORY_TITLE}"; nothing before or after it.

Shape:
{MEMORY_TITLE}
## Open now
What is still unresolved or worth watching, one bullet each, newest first, each with the monitor or customer it is \
about. Drop what the entries show resolved. "Nothing open." when there is nothing.
## Monitors
One "### <monitor name> (<id>)" per monitor that has paged, most recent first. Under each, short lines:
- what it watches and what it usually turns out to be ("usually a false alarm: ...", "real when ...");
- "Paged N times, <first> to <last>" from the counts below;
- what to check first next time;
- what was ruled out;
- open tickets, PRs or follow-ups.
Fold repeats into the pattern; keep a dated line only for a page that differed from the usual.
## Known causes
Causes that recur across monitors (a deploy pattern, one customer's mailbox, a provider outage): how to recognise \
each, and what fixed it.

Rules: plain English, short lines, no filler. Facts from the entries and the current memory only, never guessed. \
When a newer entry contradicts an older one, the newer wins. Counts and ids only, never customer message text. Keep \
it under about 1,500 words: compress older detail, never drop a monitor.

Shift: {shift_line}

Counts per monitor, from every entry:
{counts}

The memory as it stands:
{current or "(none yet: build it from the entries)"}

New entries, oldest first:
""" + "\n\n".join(fmt(e, 4000) for e in new)


def clean_memory(text: str) -> str | None:
    """The agent's answer as a memory document, or None when it is not one."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0].strip()
    start = text.find(MEMORY_TITLE)
    if start < 0:
        return None
    text = text[start:].strip()
    return text if len(text) > len(MEMORY_TITLE) + 40 else None


def save_memory(text: str, through: dict[str, Any]) -> None:
    """Write the new memory and the entry it reaches; the previous one is kept as memory.prev.md."""
    folder().mkdir(parents=True, exist_ok=True)
    if memory_file().exists():
        memory_file().replace(folder() / "memory.prev.md")
    tmp = memory_file().with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(text.strip() + "\n")
    tmp.replace(memory_file())
    state = {"through": through["id"], "through_at": through["at"],
             "updated": datetime.now(UTC).isoformat(timespec="seconds")}
    memory_state_file().write_text(json.dumps(state) + "\n")


def reset_memory() -> None:
    """Make the next rewrite start over from every entry."""
    if memory_file().exists():
        memory_file().replace(folder() / "memory.prev.md")
    memory_state_file().unlink(missing_ok=True)


def shift_line(items: Sequence[dict[str, Any]]) -> str:
    start, end = shift(items)
    if start is None:
        return "no shift recorded"
    return f"on call since {pacific(start)}" if end is None else f"not on call; last shift {pacific(start)} to {pacific(end)}"


def digest(monitors: Iterable[str] = (), limit: int = 8000, items: Sequence[dict[str, Any]] | None = None) -> str:
    """What an investigation reads first: the memory, then any entries newer than it (the paging monitors' first).
    Before there is a memory, the entries themselves."""
    items = entries() if items is None else list(items)
    memory = read_memory()
    if memory is None:
        return log_digest(monitors, limit, items)
    wanted = monitor_ids(monitors)
    newer = list(reversed(unconsolidated(items)))
    newer.sort(key=lambda e: not (wanted and monitor_ids(e.get("monitors", [])) & wanted))
    parts = [f"Shift: {shift_line(items)}", "", memory]
    if newer:
        parts += ["", "Newer than the memory (not folded in yet), newest first:"] + [fmt(e, 1500) for e in newer[:5]]
    text = "\n".join(parts)
    return text if len(text) <= limit else text[:limit].rstrip() + "\n… (cut; `search` finds the entries)"


def listing(query: str | None = None, limit: int = 200) -> dict[str, Any]:
    """The dashboard's view: the shift, then entries newest first (shift marks included), or a search."""
    items = entries()
    start, end = shift(items)
    shown = search(query, items) if query and query.strip() else list(reversed(items))
    state = memory_state()
    return {
        "memory": read_memory(),
        "memory_updated": pacific(datetime.fromisoformat(state["updated"])) if state.get("updated") else None,
        "pending": len(unconsolidated(items)),
        "shift": {"on": start is not None and end is None,
                  "start": pacific(start) if start else None, "end": pacific(end) if end else None},
        "count": sum(1 for e in items if e["kind"] in ("investigation", "note")),
        "matches": len(shown),
        "entries": [{**e, "when": pacific(when(e))} for e in shown[:limit]],
    }


# --------------------------------------------------------------------------- writing


def add(kind: str, text: str = "", *, source: str, monitors: Iterable[str] = (), link: str | None = None,
        session: str | None = None, at: datetime | None = None) -> dict[str, Any]:
    """Append one entry (and re-render notebook.md). Safe from several processes at once."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
    text = str(text or "").strip()
    if kind in ("investigation", "note") and not text:
        raise ValueError("an empty note says nothing")
    entry: dict[str, Any] = {
        "id": "n_" + secrets.token_hex(4),
        "at": (at or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds"),
        "kind": kind,
        "source": source,
        "monitors": sorted({str(m).strip() for m in monitors if str(m).strip()}),
        "text": text if len(text) <= MAX_TEXT else text[:MAX_TEXT].rstrip() + " …",
    }
    if link:
        entry["link"] = link
    if session:
        entry["session"] = session
    folder().mkdir(parents=True, exist_ok=True)
    with (folder() / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with notes_file().open("a") as out:
            out.write(json.dumps(entry) + "\n")
        render()
    return entry


def mark_shift(on: bool, *, start: datetime | None = None, source: str = "hub (Datadog On-Call)") -> dict[str, Any] | None:
    """Record a shift starting or ending, only when that changes what the notebook says. ``start`` is when the
    shift really began (Datadog's schedule); a notebook that has it starting later is corrected."""
    items = entries()
    began, ended = shift(items)
    if on:
        if began is not None and ended is None:
            if start is None or began <= start + timedelta(minutes=1):
                return None
        return add("shift_start", source=source, at=start)
    if began is None or ended is not None:
        return None
    return add("shift_end", source=source)


def render() -> None:
    """notebook.md: the digest, then every earlier entry in full (newest first, up to RENDERED_ENTRIES)."""
    items = entries()
    start, end = shift(items)
    in_shift = [e for e in items if start and when(e) >= start and (end is None or when(e) <= end)]
    rest = [e for e in reversed(items) if e["kind"] in ("investigation", "note") and e not in in_shift]
    lines = ["# On-call notebook", "", "Written by the agents hub and Aakif's sessions; the source of truth is "
             "notes.jsonl beside this file.", "", log_digest(items=items, limit=20000)]
    if rest:
        lines += ["", "## Everything earlier, newest first", ""]
        lines += [fmt(e, 4000) for e in rest[:RENDERED_ENTRIES]]
        if len(rest) > RENDERED_ENTRIES:
            lines.append(f"\n({len(rest) - RENDERED_ENTRIES} older entries are only in notes.jsonl)")
    tmp = rendered_file().with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(rendered_file())


# --------------------------------------------------------------------------- commands


def command(words: Sequence[str], *, source: str) -> str:
    """`oncall [show [--monitor ID]...] | search <words> | note <text> [--monitor "ID name"]...`, as a reply."""
    words = list(words)
    cmd = words[0] if words and words[0] in ("show", "search", "note", "rebuild") else "show"
    rest = words[1:] if words and words[0] in ("show", "search", "note", "rebuild") else words
    if cmd == "rebuild":
        reset_memory()
        return "the on-call memory will be rebuilt from every entry within a minute (the hub does it)"
    monitors: list[str] = []
    while "--monitor" in rest:
        i = rest.index("--monitor")
        if i + 1 < len(rest):
            monitors.append(rest[i + 1])
        rest = rest[:i] + rest[i + 2:]
    if cmd == "note":
        if not " ".join(rest).strip():
            return "usage: oncall note <what was found, ruled out or done> [--monitor \"ID name\"]"
        entry = add("note", " ".join(rest), source=source, monitors=monitors)
        return f"saved to the on-call notebook ({entry['id']})"
    if cmd == "search":
        if not rest:
            return "usage: oncall search <words>"
        found = search(rest)
        return "\n".join(fmt(e, 600) for e in found[:15]) if found else f"nothing in the on-call notebook for {' '.join(rest)!r}"
    return digest(monitors=monitors + [w for w in rest if re.fullmatch(r"\d+", w)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oncall_notes", description="Aakif's on-call notebook.")
    parser.add_argument("words", nargs="*", help="show | search <words> | note <text>")
    parser.add_argument("--monitor", action="append", default=[], help="monitor id (or 'id name'), repeatable")
    parser.add_argument("--source", default="terminal", help="who is writing (for notes)")
    args = parser.parse_args(argv)
    words = list(args.words)
    for monitor in args.monitor:
        words += ["--monitor", monitor]
    try:
        print(command(words, source=args.source))
    except ValueError as exc:
        print(exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
