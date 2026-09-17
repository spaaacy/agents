#!/usr/bin/env python3
"""Name Claude Code sessions the way I do by hand:
`dev: <ticket|#PR>`, `review: <#PR|branch>`, `watch: <#PR|branch>`, with ` · <repo>` when the
launch directory is not the backend (a ticket spans backend + web worktrees; PR numbers collide
across repos).

SessionStart mode reads the branch of the launch directory. UserPromptSubmit mode watches for
`/review <target>` and `/watch <target>` (always retitle: the user asked for that mode) and, on a
branch with no ticket, takes the ticket key from the first prompt that mentions one (once per
session, tracked by a marker file). Both emit `sessionTitle` (same effect as /rename) and print
nothing when there is nothing to say. A title the user set explicitly is never overwritten.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

TICKET = re.compile(r"\b([A-Za-z]{2,}-\d+)\b")
DEFAULT_BRANCHES = {"development", "main", "master", "staging", "production", "HEAD"}
COMMAND_PROMPT = re.compile(r"^\s*/(review|watch)\b\s*(.*)$", re.DOTALL)
PR_REF = re.compile(r"(?:^|/pull/)#?(\d+)\b")
REPO_LABELS = {
    "envoyai-backend": None,  # the default; unlabeled
    "envoyai-web": "web",
    "envoyai-chrome-plugin": "plugin",
    "envoyai-chat-ui": "chat-ui",
}
MARKERS = Path(__file__).resolve().parent.parent / ".session-titles"
MARKER_TTL_SECONDS = 30 * 24 * 3600


def _run(args: list[str], cwd: str) -> Optional[str]:
    try:
        out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def git_branch(cwd: str) -> Optional[str]:
    return _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd)


def gh_pr_number(cwd: str) -> Optional[str]:
    return _run(["gh", "pr", "view", "--json", "number", "--jq", ".number"], cwd)


def repo_label(cwd: str) -> Optional[str]:
    url = _run(["git", "remote", "get-url", "origin"], cwd)
    if not url:
        return None
    name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    return REPO_LABELS.get(name, name)


def ticket_key(text: str) -> Optional[str]:
    match = TICKET.search(text)
    return match.group(1).upper() if match else None


def with_repo(title: Optional[str], repo: Optional[str]) -> Optional[str]:
    return f"{title} · {repo}" if title and repo else title


def dev_title(branch: Optional[str], pr_lookup: Callable[[], Optional[str]]) -> Optional[str]:
    """`dev: ENG-2029` from the branch, else `dev: #2468` from its open PR, else nothing."""
    if not branch or branch in DEFAULT_BRANCHES:
        return None
    ticket = ticket_key(branch)
    if ticket:
        return f"dev: {ticket}"
    pr = pr_lookup()
    return f"dev: #{pr}" if pr else None


def command_title(
    prompt: str, branch: Optional[str], pr_lookup: Callable[[], Optional[str]]
) -> Optional[str]:
    """`review: #2468` for `/review 2468` or a PR URL; `review: <branch>` for a branch; the current
    branch's PR or ticket when the command has no argument. Same for `/watch`."""
    match = COMMAND_PROMPT.match(prompt)
    if not match:
        return None
    mode, rest = match.group(1), match.group(2).strip()
    target = rest.split()[0] if rest else ""
    if target:
        pr = PR_REF.search(target)
        return f"{mode}: #{pr.group(1)}" if pr else f"{mode}: {target}"
    pr = pr_lookup()
    if pr:
        return f"{mode}: #{pr}"
    ticket = ticket_key(branch or "")
    return f"{mode}: {ticket}" if ticket else None


def prompt_ticket_title(prompt: str, branch: Optional[str]) -> Optional[str]:
    """`dev: ENG-2100` from a prompt mentioning a ticket, only when the branch names none."""
    if branch and ticket_key(branch):
        return None
    ticket = ticket_key(prompt)
    return f"dev: {ticket}" if ticket else None


def marker(session_id: Optional[str]) -> Optional[Path]:
    return MARKERS / session_id if session_id else None


def mark_titled(session_id: Optional[str]) -> None:
    path = marker(session_id)
    if path is None:
        return
    MARKERS.mkdir(exist_ok=True)
    path.touch()
    cutoff = time.time() - MARKER_TTL_SECONDS
    for old in MARKERS.iterdir():
        if old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)


def already_titled(session_id: Optional[str]) -> bool:
    path = marker(session_id)
    return path is not None and path.exists()


def emit(event: str, title: Optional[str], session_id: Optional[str]) -> None:
    if title:
        mark_titled(session_id)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "sessionTitle": title}}))


def main(mode: str) -> None:
    payload = json.load(sys.stdin)
    cwd = payload.get("cwd") or "."
    session_id = payload.get("session_id")
    if mode == "session-start":
        if payload.get("session_title"):
            mark_titled(session_id)  # named already (by hand or by us earlier); hands off
            return
        if payload.get("source") not in (None, "startup", "resume", "fork"):
            return
        title = dev_title(git_branch(cwd), lambda: gh_pr_number(cwd))
        emit("SessionStart", with_repo(title, repo_label(cwd)), session_id)
    elif mode == "prompt":
        prompt = payload.get("prompt", "")
        branch = git_branch(cwd)
        title = command_title(prompt, branch, lambda: gh_pr_number(cwd))
        if title is None and not already_titled(session_id):
            title = prompt_ticket_title(prompt, branch)
        emit("UserPromptSubmit", with_repo(title, repo_label(cwd)), session_id)
    else:
        raise SystemExit(f"usage: {sys.argv[0]} session-start|prompt")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
