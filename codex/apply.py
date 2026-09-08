#!/usr/bin/env python3
"""Apply Codex instruction discovery settings while preserving other settings."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tomllib

REMOVE = object()


def edit_setting(text: str, key: str, value, *, section: str | None = None) -> str:
    """Replace one assignment, accepting the edit only if all other data matches.

    tomllib has no writer. Keep comments, credentials and unrelated formatting
    intact instead of rebuilding the user's config. Unusual TOML layouts that
    cannot be edited safely are rejected before any file is written.
    """
    original = tomllib.loads(text)
    expected = deepcopy(original)
    target = expected if section is None else expected.setdefault(section, {})
    if value is REMOVE:
        if key not in target:
            return text
        del target[key]
    else:
        target[key] = value
    if expected == original:
        return text
    if section is None and key not in original:
        candidate = f"{key} = {json.dumps(value)}\n" + text
        assert tomllib.loads(candidate) == expected
        return candidate

    pattern = rf"(?m)^[ \t]*(?:{re.escape(key)}|\"{re.escape(key)}\"|'{re.escape(key)}')[ \t]*="
    for match in re.finditer(pattern, text):
        # Read through a multiline array/string until this assignment parses.
        for boundary in re.finditer(r"\n|\Z", text[match.start():]):
            end = match.start() + boundary.end()
            assignment = text[match.start():end]
            try:
                tomllib.loads(assignment)
            except tomllib.TOMLDecodeError:
                continue
            replacement = "" if value is REMOVE else f"{key} = {json.dumps(value)}\n"
            candidate = text[:match.start()] + replacement + text[end:]
            try:
                if tomllib.loads(candidate) == expected:
                    return candidate
            except tomllib.TOMLDecodeError:
                pass
            break
    raise ValueError(f"Cannot safely update {section + '.' if section else ''}{key}")


def merge(text: str) -> str:
    config = tomllib.loads(text)
    fallbacks = config.get("project_doc_fallback_filenames", [])
    if "CLAUDE.md" not in fallbacks:
        text = edit_setting(text, "project_doc_fallback_filenames", [*fallbacks, "CLAUDE.md"])
    # The backend's CLAUDE.md alone is 84 KB; Codex defaults to 32 KiB.
    # Preserve an explicitly chosen budget, including a deliberately smaller one.
    if "project_doc_max_bytes" not in config:
        text = edit_setting(text, "project_doc_max_bytes", 131072)
    text = edit_setting(text, "skills", REMOVE, section="features")
    tomllib.loads(text)
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    codex_dir = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument("--config", type=Path, default=codex_dir / "config.toml")
    args = parser.parse_args()
    path = args.config
    original = path.read_text() if path.exists() else ""
    updated = merge(original)
    if updated == original:
        print(f"Codex instruction settings already configured: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated)
    print(f"Configured Codex instruction discovery: {path}")


if __name__ == "__main__":
    main()
