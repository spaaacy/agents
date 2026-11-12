"""Aakif's writing rules for unattended runs, read fresh from ~/.agents/AGENTS.md.

The hub's agents run without his saved settings, so his per-message hook never
fires for them. These helpers give them the same "Communication style" and
"Voice" sections: once as instructions (voice_rules) and again after each
request (turn_reminder), because rules said once fade over a long answer.
"""
from __future__ import annotations

from pathlib import Path

AGENTS_MD = Path.home() / ".agents" / "AGENTS.md"
VOICE_SECTIONS = ("Communication style", "Voice")
VOICE_PREAMBLE = """\
Aakif's own writing rules follow, the same ones his per-message hook gives \
every agent. They were written for replies to him; here they shape your Slack \
reply, whoever asked. Where they say Slack posts for other people stay in \
professional English, ignore that: he wants this bot to sound like him. Skip \
anything about files, skills or tasks.
"""


def sections(agents_md: Path | None = None, suffix: str = "") -> list[str]:
    try:
        text = (agents_md or AGENTS_MD).read_text()
    except OSError:
        return []
    parts = []
    for section in VOICE_SECTIONS:
        heading = f"## {section}\n"
        if text.count(heading) == 1:
            body = text.split(heading, 1)[1].split("\n## ", 1)[0].strip()
            if body:
                parts.append(f"{section}{suffix}:\n{body}")
    return parts


def voice_rules(agents_md: Path | None = None, preamble: str = VOICE_PREAMBLE) -> str:
    """The sections as instructions; empty when AGENTS.md has none (an answer never fails over it)."""
    parts = sections(agents_md)
    return preamble + "\n" + "\n\n".join(parts) if parts else ""


def turn_reminder(agents_md: Path | None = None) -> str:
    """The sections as his per-message hook sends them, for the end of every request."""
    parts = sections(agents_md, " (standing rule)")
    if not parts:
        return ""
    return "Write the reply under his rules, as on every message he sends you:\n\n" + "\n\n".join(parts)
