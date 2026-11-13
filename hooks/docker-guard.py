#!/usr/bin/env python3
"""PreToolUse guard: refuse shell commands that delete Docker containers, images, volumes or local DB data.

Why: on 2026-11-13 an overnight session told to "turn down the envoyai-backend container" ran
`docker compose down`, which deletes the containers; bringing them back with `up` then tried to
rebuild the api image and failed. Pausing is `docker compose stop` (resume: `docker compose start`).

Shared by Claude Code (settings.json PreToolUse, matcher Bash), Codex (~/.codex/hooks.json) and the
agents hub's Codex homes. Reads the hook's JSON on stdin; when the command deletes something, prints
a deny decision (the same wire format both clients read) and exits 0. Anything it can't read is let
through: this guards against a misread instruction, not against someone working around it on purpose.

What counts as deleting:
- docker compose / docker-compose: down, rm
- docker: rm, rmi, system prune, and container|image|volume rm|remove|prune
- rm on a path containing "dockerdata" (the backend's Postgres data lives in dockerdata/postgres-data)
Stopping, killing, restarting and `up` (which recreates containers from the existing images) pass.
Commands are read the way a shell would: quoted text stays one word (so `grep 'docker compose down'`
passes), heredoc bodies are skipped, and `sh -c` / `bash -lc` / `eval` scripts are read too.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

SEPARATORS = {";", "&", "&&", "|", "||", "|&", ";;", "(", ")", "\n", "`"}
WRAPPERS = {"sudo", "env", "command", "exec", "time", "nohup", "nice", "caffeinate", "builtin", "noglob"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
# Flags that take a value, before or after `compose`; their value is never the subcommand.
VALUE_FLAGS = {
    "-f", "--file", "-p", "--project-name", "--project-directory", "--env-file", "--profile",
    "--ansi", "--progress", "--parallel", "-H", "--host", "-c", "--context", "--config",
    "-l", "--log-level", "--tlscacert", "--tlscert", "--tlskey",
}
DOCKER_MANAGED = {"container", "image", "volume"}
DELETE_WORDS = {"rm", "remove", "prune"}
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

REASON = (
    "Blocked by ~/.agents/hooks/docker-guard.py: `{cmd}` deletes Docker containers, images, volumes or "
    "local database data. To pause the stack (\"turn down\", \"pause\", \"stop\", \"shut down\"), run "
    "`docker compose stop`; `docker compose start` resumes it, and if containers ever have to be recreated "
    "use `docker compose up -d --no-build` so the existing images are reused. Deleting containers, images, "
    "volumes or dockerdata is Aakif's to run himself: if he explicitly asked for that, give him the exact "
    "command to run in his own terminal and stop."
)


def strip_heredocs(script: str) -> str:
    """Drop heredoc bodies (their lines are data, not commands)."""
    out, lines, i = [], script.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        delims = [m.group(2) for m in HEREDOC.finditer(line)]
        i += 1
        for delim in delims:
            while i < len(lines) and lines[i].strip() != delim:
                i += 1
            i += 1  # the closing delimiter line
    return "\n".join(out)


def tokens(script: str) -> list[str] | None:
    lex = shlex.shlex(script, posix=True, punctuation_chars=";&|()<>\n`")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = "#"
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes
        return None


def segments(toks: list[str]) -> list[list[str]]:
    out, cur = [], []
    for tok in toks:
        if tok in SEPARATORS or (tok and set(tok) <= set(";&|()\n`")):
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def words_after(args: list[str]) -> list[str]:
    """The non-flag words of a docker command line, skipping each value-taking flag's value."""
    words, skip = [], False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in VALUE_FLAGS:
            skip = True
            continue
        if arg.startswith("-") or arg in {"<", ">", ">>", "<<", ">&", "<&", "2>", "&>"}:
            continue
        words.append(arg)
    return words


def docker_deletes(prog: str, args: list[str]) -> bool:
    words = words_after(args)
    if prog == "docker-compose":
        return bool(words) and words[0] in {"down", "rm"}
    if not words:
        return False
    if words[0] == "compose":
        return len(words) > 1 and words[1] in {"down", "rm"}
    if words[0] in {"rm", "rmi"}:
        return True
    if words[0] == "system":
        return len(words) > 1 and words[1] == "prune"
    if words[0] in DOCKER_MANAGED:
        return len(words) > 1 and words[1] in DELETE_WORDS
    return False


def segment_deletes(seg: list[str], depth: int) -> bool:
    i = 0
    while i < len(seg):  # skip env assignments and wrappers to reach the command itself
        tok = seg[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok) or os.path.basename(tok) in WRAPPERS:
            i += 1
            continue
        if tok in {"timeout", "gtimeout"}:
            i += 2
            continue
        break
    if i >= len(seg):
        return False
    prog, args = os.path.basename(seg[i]), seg[i + 1:]
    if prog in {"docker", "docker-compose"}:
        return docker_deletes(prog, args)
    if prog == "rm":
        return any("dockerdata" in a for a in args if not a.startswith("-"))
    if prog == "eval":
        return deletes(" ".join(args), depth + 1)
    if prog in SHELLS:
        for j, arg in enumerate(args):
            if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", arg) and j + 1 < len(args):
                return deletes(args[j + 1], depth + 1)
    if prog == "xargs":  # `... | xargs docker rm`
        rest = [a for a in args if not a.startswith("-")]
        if rest and os.path.basename(rest[0]) in {"docker", "docker-compose"}:
            return docker_deletes(os.path.basename(rest[0]), rest[1:])
    return False


def deletes(script: str, depth: int = 0) -> bool:
    if depth > 4 or not script.strip():
        return False
    toks = tokens(strip_heredocs(script))
    if toks is None:
        return False
    return any(segment_deletes(seg, depth) for seg in segments(toks))


def command_of(event: dict) -> str | None:
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    cmd = tool_input.get("command", tool_input.get("cmd"))
    if isinstance(cmd, list):  # Codex can pass argv, e.g. ["bash", "-lc", "..."]
        return shlex.join(str(c) for c in cmd)
    return cmd if isinstance(cmd, str) else None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    cmd = command_of(event) if isinstance(event, dict) else None
    if not cmd or not deletes(cmd):
        return 0
    short = " ".join(cmd.split())
    short = short if len(short) <= 160 else short[:157] + "..."
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": REASON.format(cmd=short),
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
