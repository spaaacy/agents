#!/usr/bin/env python3
"""Name Claude Code sessions the way I do by hand: `dev: <ticket|#PR>` and `review: <#PR|branch>`.

SessionStart mode reads the branch of the launch directory; UserPromptSubmit mode watches
for `/review <target>`. Both emit `sessionTitle` (same effect as /rename) and print nothing
when there is nothing to say. A title the user set explicitly is never overwritten.
"""

import json
import re
import subprocess
import sys
from typing import Callable, Optional

TICKET = re.compile(r"\b([A-Za-z]{2,}-\d+)\b")
DEFAULT_BRANCHES = {"development", "main", "master", "staging", "production", "HEAD"}
REVIEW_PROMPT = re.compile(r"^\s*/review\b\s*(.*)$", re.DOTALL)
PR_REF = re.compile(r"(?:^|/pull/)#?(\d+)\b")


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


def ticket_key(text: str) -> Optional[str]:
    match = TICKET.search(text)
    return match.group(1).upper() if match else None


def dev_title(
    branch: Optional[str], pr_lookup: Callable[[], Optional[str]]
) -> Optional[str]:
    """`dev: ENG-2029` from the branch, else `dev: #2468` from its open PR, else nothing."""
    if not branch or branch in DEFAULT_BRANCHES:
        return None
    ticket = ticket_key(branch)
    if ticket:
        return f"dev: {ticket}"
    pr = pr_lookup()
    return f"dev: #{pr}" if pr else None


def review_title(
    prompt: str, branch: Optional[str], pr_lookup: Callable[[], Optional[str]]
) -> Optional[str]:
    """`review: #2468` for `/review 2468` or a PR URL; `review: <branch>` for a branch;
    the current branch's PR or ticket when `/review` has no argument."""
    match = REVIEW_PROMPT.match(prompt)
    if not match:
        return None
    target = match.group(1).strip().split()[0] if match.group(1).strip() else ""
    if target:
        pr = PR_REF.search(target)
        if pr:
            return f"review: #{pr.group(1)}"
        return f"review: {target}"
    pr = pr_lookup()
    if pr:
        return f"review: #{pr}"
    ticket = ticket_key(branch or "")
    return f"review: {ticket}" if ticket else None


def emit(event: str, title: Optional[str]) -> None:
    if title:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "sessionTitle": title}}))


def main(mode: str) -> None:
    payload = json.load(sys.stdin)
    cwd = payload.get("cwd") or "."
    if mode == "session-start":
        if payload.get("session_title"):
            return  # the user named it; leave it alone
        if payload.get("source") not in (None, "startup", "resume", "fork"):
            return
        emit("SessionStart", dev_title(git_branch(cwd), lambda: gh_pr_number(cwd)))
    elif mode == "prompt":
        emit(
            "UserPromptSubmit",
            review_title(payload.get("prompt", ""), git_branch(cwd), lambda: gh_pr_number(cwd)),
        )
    else:
        raise SystemExit(f"usage: {sys.argv[0]} session-start|prompt")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
