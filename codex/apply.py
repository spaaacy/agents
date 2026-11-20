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

# Every skill creates its worktrees here (/pickup, /fix, /address-review).
# Outside cwd, workspace-write asks before each edit there; .git and the
# gitdir target stay read-only either way, so commits still ask.
WORKTREES = str(Path.home() / "dev" / ".claude" / "worktrees")
SANDBOX = "sandbox_workspace_write"


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


def ensure_writable_root(text: str, root: str) -> str:
    """Add ``root`` to sandbox_workspace_write.writable_roots, keeping any others."""
    config = tomllib.loads(text)
    section = config.get(SANDBOX)
    if section is not None:
        roots = section.get("writable_roots", [])
        if root in roots:
            return text
        if "writable_roots" in section:
            return edit_setting(text, "writable_roots", [*roots, root], section=SANDBOX)
        return add_to_section(text, "writable_roots", [root])
    expected = deepcopy(config)
    expected[SANDBOX] = {"writable_roots": [root]}
    block = f"[{SANDBOX}]\nwritable_roots = {json.dumps([root])}\n\n"
    # Before the mcp/render.py block, which owns everything up to its END line.
    marker = text.find("# BEGIN agents-managed")
    if marker >= 0:
        candidate = text[:marker] + block + text[marker:]
    else:
        candidate = text + ("\n" if text and not text.endswith("\n") else "") + ("\n" if text else "") + block
    if tomllib.loads(candidate) != expected:
        raise ValueError("Cannot safely add sandbox_workspace_write.writable_roots")
    return candidate


def add_to_section(text: str, key: str, value, section: str = SANDBOX) -> str:
    """Add a key a section does not have yet, right under its header."""
    expected = deepcopy(tomllib.loads(text))
    expected[section][key] = value
    header = re.search(rf"(?m)^[ \t]*\[{re.escape(section)}\][ \t]*(#.*)?\n", text)
    if header is None:
        raise ValueError(f"Cannot safely add {section}.{key}")
    candidate = text[:header.end()] + f"{key} = {json.dumps(value)}\n" + text[header.end():]
    if tomllib.loads(candidate) != expected:
        raise ValueError(f"Cannot safely add {section}.{key}")
    return candidate


def ensure_feature(text: str, key: str, value) -> str:
    """Set one [features] switch, adding the key or the section when missing."""
    config = tomllib.loads(text)
    features = config.get("features")
    if features is not None:
        if features.get(key) == value:
            return text
        if key in features:
            return edit_setting(text, key, value, section="features")
        return add_to_section(text, key, value, section="features")
    expected = deepcopy(config)
    expected["features"] = {key: value}
    block = f"[features]\n{key} = {json.dumps(value)}\n\n"
    marker = text.find("# BEGIN agents-managed")
    if marker >= 0:
        candidate = text[:marker] + block + text[marker:]
    else:
        candidate = text + ("\n" if text and not text.endswith("\n") else "") + ("\n" if text else "") + block
    if tomllib.loads(candidate) != expected:
        raise ValueError(f"Cannot safely add features.{key}")
    return candidate


def ensure_network_access(text: str) -> str:
    """Let sandboxed commands reach the network (gh, git push and fetch, evals, npm) without asking.

    Aakif's call, 2026-11-06: about 450 of 1,138 asks in one Codex session were
    network commands. Writes outside the writable roots and git commits still ask.
    """
    section = tomllib.loads(text)[SANDBOX]
    if section.get("network_access") is True:
        return text
    if "network_access" in section:
        return edit_setting(text, "network_access", True, section=SANDBOX)
    return add_to_section(text, "network_access", True)


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
    # The question tool (request_user_input) is Plan-mode only by default. Aakif's sign-offs (a schema change,
    # a ticket create) go through it, so it is on in Default mode too.
    text = ensure_feature(text, "default_mode_request_user_input", True)
    text = ensure_writable_root(text, WORKTREES)
    text = ensure_network_access(text)
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
