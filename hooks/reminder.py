#!/usr/bin/env python3
"""Emit shared AGENTS.md sections in the Claude Code / Codex hook format."""

import json
from pathlib import Path
import sys


def reminder(section: str, root: Path) -> dict:
    if section == "Voice (default on)" and (root / ".brainrot-off").exists():
        context = (
            "Voice (standing rule): brainrot is off. Use normal professional "
            "peer-engineer prose without slang or the persona until turned back on."
        )
    else:
        instructions = (root / "AGENTS.md").read_text()
        heading = f"## {section}\n"
        if instructions.count(heading) != 1:
            raise ValueError(f"Expected exactly one {heading.strip()} in AGENTS.md")
        content = instructions.split(heading, 1)[1].split("\n## ", 1)[0].strip()
        if not content:
            raise ValueError(f"Empty {section} section in AGENTS.md")
        context = f"{section} (standing rule):\n{content}"
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        },
        "suppressOutput": True,
    }


if __name__ == "__main__":
    print(json.dumps(reminder(sys.argv[1], Path(__file__).resolve().parent.parent)))
