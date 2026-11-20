#!/usr/bin/env python3
"""Schema guard: no new or dropped table or column in envoyai-backend without Aakif's approval.

Why: agents kept adding tables and columns he never saw (raised 2026-11-20). A database change is his
call, the same as a product change (`Working style` in ~/.agents/AGENTS.md), and this is the backstop
for an agent that forgot to ask.

One script, three entry points:
- PreToolUse (no argument). Refuses, and stores what it found as the pending request:
  * a Write / Edit / MultiEdit of a file under alembic/versions/ whose upgrade adds or drops a table or
    column not approved for that checkout's branch;
  * a Codex apply_patch doing the same (read from the patch's added lines, best effort);
  * a shell `git commit` or `git push` in an envoyai-backend checkout whose branch carries such a change
    against origin/development (a commit reads the whole worktree, untracked files included, because
    `git add x && git commit` stages in the same command);
  * any write to the approvals folder: an agent never approves for him.
- PostToolUse on the question tool (`--record`; Claude Code's AskUserQuestion, Codex's request_user_input):
  a question headed "Schema" that he answered with an option whose label starts with "Approve" records the
  changes its diagram lists (the option's preview, else the question text), for the branch the diagram
  names (format: ~/.agents/skills/db-diff/diagram.md). An answer it cannot read is kept in
  unread-answers.jsonl beside the approvals.
- UserPromptSubmit (`--prompt`): him typing "approve schema" approves the newest pending request. Agents
  never ask for it (they use the question tool); it is his own shortcut.

Only new and dropped tables and columns (and renames, which drop one name and add another) need approval;
indexes, constraints, type changes and enum values do not. Anything it cannot read is let through, as
docker-guard does: this stops an agent that forgot to ask, not someone working around it on purpose.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = "envoyai-backend"
BASE = "origin/development"
MIGRATIONS = "alembic/versions"
STATE = Path(os.environ.get("SCHEMA_GUARD_STATE", Path.home() / ".agents/state/schema-approvals"))
PENDING = STATE / "pending.json"
PENDING_FOR = 24 * 3600
READ_ONLY = {"cat", "ls", "head", "tail", "jq", "grep", "rg", "find", "wc", "stat", "file", "less"}
GIT_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
OPS = {"create_table", "drop_table", "add_column", "drop_column", "rename_table", "alter_column", "execute"}
NOT_A_COLUMN = {"constraint", "primary", "unique", "foreign", "check", "exclude", "value"}

_spec = importlib.util.spec_from_file_location("docker_guard", Path(__file__).resolve().parent / "docker-guard.py")
shell = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shell)


# --------------------------------------------------------------------------- reading a migration


def _name(raw: str) -> str:
    """A SQL or Python identifier as it is stored: no quotes, no schema prefix."""
    return raw.strip().strip('"').split(".")[-1].strip('"').lower()


def sql_changes(sql: str) -> set[str]:
    out: set[str] = set()
    for m in re.finditer(r"\bcreate\s+(?:unlogged\s+)?table\s+(?:if\s+not\s+exists\s+)?([\w.\"]+)", sql, re.I):
        out.add(f"add table {_name(m.group(1))}")
    for m in re.finditer(r"\bdrop\s+table\s+(?:if\s+exists\s+)?([\w.\", ]+?)(?:\s+cascade|\s+restrict|;|$)", sql, re.I | re.M):
        for table in m.group(1).split(","):
            if table.strip():
                out.add(f"drop table {_name(table)}")
    for m in re.finditer(r"\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?([\w.\"]+)\s+(.*?)(?:;|$)", sql, re.I | re.S):
        table, depth, actions, cur = _name(m.group(1)), 0, [], ""
        for ch in m.group(2):
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                actions.append(cur)
                cur = ""
            else:
                cur += ch
        actions.append(cur)
        for action in (a.strip() for a in actions):
            if r := re.match(r"rename\s+to\s+([\w\"]+)", action, re.I):
                out |= {f"drop table {table}", f"add table {_name(r.group(1))}"}
            elif r := re.match(r"rename\s+(?:column\s+)?([\w\"]+)\s+to\s+([\w\"]+)", action, re.I):
                if _name(r.group(1)) != "constraint":
                    out |= {f"drop column {table}.{_name(r.group(1))}", f"add column {table}.{_name(r.group(2))}"}
            elif r := re.match(r"add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?([\w\"]+)", action, re.I):
                if _name(r.group(1)) not in NOT_A_COLUMN:
                    out.add(f"add column {table}.{_name(r.group(1))}")
            elif r := re.match(r"drop\s+(?:column\s+)?(?:if\s+exists\s+)?([\w\"]+)", action, re.I):
                if _name(r.group(1)) not in NOT_A_COLUMN:
                    out.add(f"drop column {table}.{_name(r.group(1))}")
    return out


def _text(node: ast.AST, consts: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "x" for v in node.values)
    if isinstance(node, ast.Call) and node.args:  # sa.text("..."), text("...")
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if name == "text":
            return _text(node.args[0], consts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left, consts), _text(node.right, consts)
        return left + right if left is not None and right is not None else None
    return None


def _column_name(node: ast.AST, consts: dict[str, str]) -> str | None:
    """The name inside sa.Column("name", ...)."""
    if isinstance(node, ast.Call) and node.args:
        return _text(node.args[0], consts)
    return None


def migration_changes(text: str) -> set[str]:
    """The tables and columns a migration file's upgrade adds or drops ("add column offer.held_at")."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return regex_changes(text.split("def downgrade", 1)[0])
    consts: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = _text(node.value, consts)
            if value is not None:
                consts[node.targets[0].id] = value
    out: set[str] = set()
    for top in tree.body:
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)) and top.name == "downgrade":
            continue
        for call in ast.walk(top):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name) and call.func.value.id == "op"
                    and call.func.attr in OPS):
                continue
            op, args = call.func.attr, call.args
            kwargs = {k.arg: k.value for k in call.keywords if k.arg}
            first = _text(args[0], consts) if args else None
            if op == "execute":
                if first is not None:
                    out |= sql_changes(first)
                continue
            if first is None:
                continue  # a name it cannot read: let through
            table = _name(first)
            if op == "create_table":
                out.add(f"add table {table}")
            elif op == "drop_table":
                out.add(f"drop table {table}")
            elif op == "rename_table" and len(args) > 1 and (new := _text(args[1], consts)):
                out |= {f"drop table {table}", f"add table {_name(new)}"}
            elif op == "add_column" and len(args) > 1 and (col := _column_name(args[1], consts)):
                out.add(f"add column {table}.{_name(col)}")
            elif op == "drop_column" and len(args) > 1 and (col := _text(args[1], consts)):
                out.add(f"drop column {table}.{_name(col)}")
            elif op == "alter_column" and len(args) > 1 and "new_column_name" in kwargs:
                old, new = _text(args[1], consts), _text(kwargs["new_column_name"], consts)
                if old and new:
                    out |= {f"drop column {table}.{_name(old)}", f"add column {table}.{_name(new)}"}
    return out


def regex_changes(text: str) -> set[str]:
    """For text that does not parse (a patch's added lines): the plain op calls and SQL in it."""
    q = r"[\"']([\w.]+)[\"']"
    out = {f"add table {_name(m)}" for m in re.findall(rf"op\.create_table\(\s*{q}", text)}
    out |= {f"drop table {_name(m)}" for m in re.findall(rf"op\.drop_table\(\s*{q}", text)}
    out |= {f"add column {_name(t)}.{_name(c)}"
            for t, c in re.findall(rf"op\.add_column\(\s*{q}\s*,\s*(?:sa\.)?Column\(\s*{q}", text)}
    out |= {f"drop column {_name(t)}.{_name(c)}" for t, c in re.findall(rf"op\.drop_column\(\s*{q}\s*,\s*{q}", text)}
    return out | sql_changes(text)


# --------------------------------------------------------------------------- approvals


def _slug(branch: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "__", branch)


def approval_file(repo: str, branch: str) -> Path:
    return STATE / repo / f"{_slug(branch)}.json"


def approved(repo: str, branch: str) -> set[str]:
    try:
        data = json.loads(approval_file(repo, branch).read_text())
    except (OSError, ValueError):
        return set()
    return {c["change"] for c in data.get("changes", []) if isinstance(c, dict) and "change" in c}


def unapproved(changes: set[str], repo: str, branch: str) -> list[str]:
    ok = approved(repo, branch)
    new_tables = {c.split(" ", 2)[2] for c in ok if c.startswith("add table ")}

    def covered(change: str) -> bool:
        if change in ok:
            return True
        # A column added to a table this branch creates rides on the table's approval.
        return change.startswith("add column ") and change.split(" ", 2)[2].split(".")[0] in new_tables

    return sorted(c for c in changes if not covered(c))


def record(repo: str, branch: str, changes: list[str], via: str, session: str | None) -> None:
    path = approval_file(repo, branch)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {"repo": repo, "branch": branch, "changes": []}
    have = {c.get("change") for c in data["changes"]}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    for change in changes:
        if change not in have:
            data["changes"].append({"change": change, "approved_at": stamp, "via": via, "session": session})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def parse_diagram(text: str) -> tuple[str | None, str, list[str]]:
    """(branch, repo, changes) from the diagram an Approve option previews."""
    branch, repo, table, kind, changes = None, REPO, None, None, []
    for line in text.splitlines():
        if m := re.match(r"\s*branch:\s*(\S+)", line):
            branch = m.group(1)
        elif m := re.match(r"\s*repo:\s*(\S+)", line):
            repo = m.group(1)
        elif m := re.match(r"([A-Za-z_][\w.]*)\s+\((new|existing|dropped|renamed) table([^)]*)\)", line):
            table, kind = _name(m.group(1)), m.group(2)
            if kind == "new":
                changes.append(f"add table {table}")
            elif kind == "dropped":
                changes.append(f"drop table {table}")
            elif kind == "renamed" and (was := re.search(r"was\s+([\w.]+)", m.group(3))):
                changes += [f"add table {table}", f"drop table {_name(was.group(1))}"]
        elif (m := re.match(r"\s+([+-])\s+([A-Za-z_]\w*)", line)) and table and kind in {"existing", "renamed"}:
            verb = "add" if m.group(1) == "+" else "drop"
            changes.append(f"{verb} column {table}.{_name(m.group(2))}")
    return branch, repo, changes


# --------------------------------------------------------------------------- git


def git(cwd: str | Path, *args: str) -> str | None:
    try:
        run = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return run.stdout if run.returncode == 0 else None


def checkout_of(path: str | Path) -> tuple[str, str, str] | None:
    """(top-level dir, repo name, branch) of the checkout holding `path`, when it is envoyai-backend."""
    here = Path(path)
    while not here.is_dir():
        if here.parent == here:
            return None
        here = here.parent
    top, url, branch = git(here, "rev-parse", "--show-toplevel"), git(here, "remote", "get-url", "origin"), \
        git(here, "rev-parse", "--abbrev-ref", "HEAD")
    if not (top and url and branch):
        return None
    repo = url.strip().rstrip("/").split("/")[-1].removesuffix(".git")
    branch = branch.strip()
    if repo != REPO or branch == "HEAD":
        return None
    return top.strip(), repo, branch


def branch_changes(top: str, whole_worktree: bool) -> set[str] | None:
    """What the branch adds or drops against origin/development: committed work for a push; for a commit,
    everything in the worktree (committed, staged, changed and untracked)."""
    base = git(top, "merge-base", "HEAD", BASE)
    if not base:
        return None
    base = base.strip()
    if whole_worktree:
        names = (git(top, "diff", "--name-only", "--diff-filter=AMR", base, "--", MIGRATIONS) or "").split()
        names += (git(top, "ls-files", "--others", "--exclude-standard", "--", MIGRATIONS) or "").split()
    else:
        names = (git(top, "diff", "--name-only", "--diff-filter=AMR", base, "HEAD", "--", MIGRATIONS) or "").split()
    out: set[str] = set()
    for name in dict.fromkeys(names):
        if not name.endswith(".py"):
            continue
        if whole_worktree:
            try:
                new = (Path(top) / name).read_text()
            except OSError:
                new = git(top, "show", f":{name}") or ""
        else:
            new = git(top, "show", f"HEAD:{name}") or ""
        old = git(top, "show", f"{base}:{name}") or ""
        out |= migration_changes(new) - migration_changes(old)
    return out


# --------------------------------------------------------------------------- PreToolUse


def deny(reason: str) -> int:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 0


def refuse(repo: str, branch: str, missing: list[str], cwd: str | None) -> int:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        PENDING.write_text(json.dumps({"repo": repo, "branch": branch, "changes": missing, "at": time.time(),
                                       "cwd": cwd}, indent=2) + "\n")
    except OSError:
        pass
    listed = "; ".join(missing)
    return deny(
        f"Blocked by ~/.agents/hooks/schema-guard.py: branch {branch} ({repo}) has database changes Aakif has "
        f"not approved: {listed}. A new or dropped table or column is his call, like a product change "
        "(`Working style` in ~/.agents/AGENTS.md). Stop and ask him now with your structured question tool: "
        "AskUserQuestion in Claude Code, request_user_input in Codex. It is a product decision he asks to be "
        "given as a question, not a permission request; never ask him to type a phrase, and never put it inside "
        "a status update. The question gets header \"Schema\" and says, per change, why it has to be stored "
        "(what writes it, what reads it, why it cannot be worked out from data already stored or kept in an "
        "existing settings group or row). Options: one labelled \"Approve\" and one that does not add them. "
        f"The diagram from ~/.agents/skills/db-diff/diagram.md (first line `branch: {branch}`) goes in the "
        "Approve option's preview (AskUserQuestion) or at the end of the question text (request_user_input, "
        "whose options have no preview): the hook records exactly what that diagram lists. Retry once he has "
        "approved. If these came from development rather than this branch, run `git fetch origin development` "
        "and retry. A run with nobody to ask stops here and reports what it would add."
    )


def _apply_edit(text: str, old: str, new: str, replace_all: bool) -> str | None:
    if not old or old not in text:
        return None
    return text.replace(old, new) if replace_all else text.replace(old, new, 1)


def check_file_write(event: dict) -> int:
    tool, tin = event.get("tool_name"), event.get("tool_input") or {}
    path = tin.get("file_path") or tin.get("path")
    if not isinstance(path, str):
        return 0
    if Path(path).expanduser().resolve().is_relative_to(STATE.resolve()):
        return deny("Blocked by ~/.agents/hooks/schema-guard.py: the schema approvals folder records Aakif's own "
                    "answers and is written only by the hook. Ask him instead (see `Working style` in "
                    "~/.agents/AGENTS.md).")
    if f"/{MIGRATIONS}/" not in path or not path.endswith(".py"):
        return 0
    try:
        current = Path(path).read_text()
    except OSError:
        current = ""
    if tool == "Write":
        new = tin.get("content")
    elif tool == "Edit":
        new = _apply_edit(current, tin.get("old_string", ""), tin.get("new_string", ""), bool(tin.get("replace_all")))
    elif tool == "MultiEdit":
        new = current
        for edit in tin.get("edits") or []:
            new = _apply_edit(new or "", edit.get("old_string", ""), edit.get("new_string", ""),
                              bool(edit.get("replace_all"))) if new is not None else None
    else:
        return 0
    if not isinstance(new, str):
        return 0
    where = checkout_of(path)
    if not where:
        return 0
    top, repo, branch = where
    missing = unapproved(migration_changes(new) - migration_changes(current), repo, branch)
    return refuse(repo, branch, missing, top) if missing else 0


def check_patch(event: dict, patch: str) -> int:
    cwd = event.get("cwd") or os.getcwd()
    where = checkout_of(cwd)
    if not where:
        return 0
    top, repo, branch = where
    changes: set[str] = set()
    for m in re.finditer(r"^\*\*\* (Add|Update) File: (.+?)\n(.*?)(?=^\*\*\* |\Z)", patch, re.M | re.S):
        kind, path, body = m.groups()
        if f"{MIGRATIONS}/" not in path or not path.strip().endswith(".py"):
            continue
        added = "\n".join(line[1:] for line in body.splitlines() if line.startswith("+"))
        changes |= migration_changes(added) if kind == "Add" else regex_changes(added)
    missing = unapproved(changes, repo, branch)
    return refuse(repo, branch, missing, top) if missing else 0


def git_targets(cmd: str, cwd: str) -> list[tuple[str, str]]:
    """(directory, subcommand) for each `git commit` / `git push` in a shell command, following `cd`."""
    toks = shell.tokens(shell.strip_heredocs(cmd))
    if toks is None:
        return []
    out, here = [], cwd
    for seg in shell.segments(toks):
        i = 0
        while i < len(seg) and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[i]) or os.path.basename(seg[i]) in shell.WRAPPERS):
            i += 1
        if i >= len(seg):
            continue
        prog, args = os.path.basename(seg[i]), seg[i + 1:]
        if prog == "cd" and args:
            here = os.path.normpath(os.path.join(here, os.path.expanduser(args[0])))
        elif prog in shell.SHELLS:
            for j, arg in enumerate(args):
                if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", arg) and j + 1 < len(args):
                    out += git_targets(args[j + 1], here)
        elif prog == "git":
            where, j = here, 0
            while j < len(args) and args[j].startswith("-"):
                flag = args[j].split("=", 1)[0]
                if args[j] == "-C" and j + 1 < len(args):
                    where = os.path.normpath(os.path.join(where, os.path.expanduser(args[j + 1])))
                if flag in GIT_VALUE_FLAGS and "=" not in args[j]:
                    j += 1
                j += 1
            if j < len(args) and args[j] in {"commit", "push"}:
                out.append((where, args[j]))
    return out


def writes_approvals(cmd: str) -> bool:
    if "schema-approvals" not in cmd:
        return False
    toks = shell.tokens(shell.strip_heredocs(cmd))
    if toks is None:
        return True
    for seg in shell.segments(toks):
        if not any("schema-approvals" in t for t in seg):
            continue
        i = 0
        while i < len(seg) and os.path.basename(seg[i]) in shell.WRAPPERS:
            i += 1
        prog = os.path.basename(seg[i]) if i < len(seg) else ""
        redirected = any(tok in {">", ">>"} and i + 1 < len(seg) and "schema-approvals" in seg[i + 1]
                         for i, tok in enumerate(seg))
        if prog not in READ_ONLY or redirected:
            return True
    return False


def check_shell(event: dict, cmd: str) -> int:
    if writes_approvals(cmd):
        return deny("Blocked by ~/.agents/hooks/schema-guard.py: the schema approvals folder records Aakif's own "
                    "answers and is written only by the hook. Ask him instead (see `Working style` in "
                    "~/.agents/AGENTS.md).")
    if "git" not in cmd or not re.search(r"\b(commit|push)\b", cmd):
        return 0
    cwd = event.get("cwd") or os.getcwd()
    for where, sub in git_targets(cmd, cwd):
        found = checkout_of(where)
        if not found:
            continue
        top, repo, branch = found
        changes = branch_changes(top, whole_worktree=sub == "commit")
        if changes is None:
            continue
        missing = unapproved(changes, repo, branch)
        if missing:
            return refuse(repo, branch, missing, top)
    return 0


def pre_tool_use(event: dict) -> int:
    tin = event.get("tool_input")
    if not isinstance(tin, dict):
        return 0
    if event.get("tool_name") in {"Write", "Edit", "MultiEdit"}:
        return check_file_write(event)
    patch = next((v for v in tin.values() if isinstance(v, str) and "*** Begin Patch" in v), None)
    cmd = shell.command_of(event)
    if patch is None and cmd and "*** Begin Patch" in cmd:
        patch = cmd
    if patch is not None:
        return check_patch(event, patch)
    return check_shell(event, cmd) if cmd else 0


# --------------------------------------------------------------------------- his approval


def context(event_name: str, text: str) -> int:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}))
    return 0


def _first_label(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return _first_label(value[0])
    if isinstance(value, dict):
        for key in ("answers", "answer", "label", "selected"):
            if key in value:
                return _first_label(value[key])
    return None


def _answers(event: dict) -> dict[str, str]:
    """Question text or id -> the label he picked, from either client's payload."""
    found: dict[str, str] = {}
    for source in (event.get("tool_input"), event.get("tool_response")):
        if isinstance(source, str):
            try:
                source = json.loads(source)
            except ValueError:
                found.update(dict(re.findall(r'"([^"]+)"="([^"]+)"', source)))
                continue
        if isinstance(source, dict) and isinstance(source.get("answers"), dict):
            for key, value in source["answers"].items():
                label = _first_label(value)
                if label:
                    found[str(key)] = label
    return found


def record_question(event: dict) -> int:
    tin = event.get("tool_input") or {}
    if isinstance(tin, str):
        try:
            tin = json.loads(tin)
        except ValueError:
            return 0
    questions = [q for q in tin.get("questions") or []
                 if isinstance(q, dict) and str(q.get("header", "")).strip().lower() == "schema"]
    if not questions:
        return 0
    answers = _answers(event)
    notes = []
    for q in questions:
        label = answers.get(str(q.get("question", ""))) or answers.get(str(q.get("id", "")))
        if not label:
            _keep_unread(event)
            notes.append("Could not read Aakif's answer to the schema question, so nothing was recorded.")
            continue
        if not label.strip().lower().startswith("approve"):
            notes.append("Aakif did not approve the schema change in that question: do not add it.")
            continue
        option = next((o for o in q.get("options") or []
                       if isinstance(o, dict) and str(o.get("label", "")).strip() == label.strip()), {})
        branch, repo, changes = None, REPO, []
        for text in (option.get("preview"), option.get("description"), q.get("question")):
            if text:
                branch, repo, changes = parse_diagram(str(text))
                if branch and changes:
                    break
        if not branch or not changes:
            notes.append("No readable diagram (a `branch:` line and the tables per "
                         "~/.agents/skills/db-diff/diagram.md) in the Approve option's preview or the question, "
                         "so nothing was recorded.")
            continue
        record(repo, branch, changes, str(event.get("tool_name") or "question"), event.get("session_id"))
        notes.append(f"Recorded Aakif's approval on {branch}: {'; '.join(changes)}.")
    return context("PostToolUse", " ".join(notes)) if notes else 0


def _keep_unread(event: dict) -> None:
    """A schema answer the hook could not read is kept, so the reader can be fixed to the client's shape."""
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with (STATE / "unread-answers.jsonl").open("a") as out:
            out.write(json.dumps({"at": time.time(), "tool_name": event.get("tool_name"),
                                  "tool_input": event.get("tool_input"),
                                  "tool_response": event.get("tool_response")}, default=str) + "\n")
    except OSError:
        pass


def record_prompt(event: dict) -> int:
    prompt = str(event.get("prompt") or event.get("user_prompt") or "")
    if not re.match(r"\s*approve\s+schema\b", prompt, re.I):
        return 0
    try:
        pending = json.loads(PENDING.read_text())
    except (OSError, ValueError):
        pending = None
    if not pending or time.time() - float(pending.get("at", 0)) > PENDING_FOR:
        return context("UserPromptSubmit", "Aakif said \"approve schema\", but no schema change is waiting "
                       "(none was blocked in the last day): nothing was recorded.")
    record(pending["repo"], pending["branch"], pending["changes"], "prompt", event.get("session_id"))
    PENDING.unlink(missing_ok=True)
    return context("UserPromptSubmit", f"Recorded Aakif's approval on {pending['branch']}: "
                   f"{'; '.join(pending['changes'])}.")


def list_checkout(path: str, committed: bool) -> int:
    """`--list <dir> [--committed]`: what /db-diff reads, as JSON, through the same reader the guard uses."""
    found = checkout_of(os.path.expanduser(path))
    if not found:
        print(json.dumps({"error": f"{path} is not an envoyai-backend checkout on a branch"}))
        return 1
    top, repo, branch = found
    changes = branch_changes(top, whole_worktree=not committed)
    base = (git(top, "merge-base", "HEAD", BASE) or "").strip() or None
    print(json.dumps({
        "dir": top, "repo": repo, "branch": branch, "base": base,
        "changes": sorted(changes or []), "approved": sorted(approved(repo, branch)),
        "missing": unapproved(changes or set(), repo, branch),
        "approvals_file": str(approval_file(repo, branch)),
    }, indent=2))
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--list" and len(sys.argv) > 2:
        return list_checkout(sys.argv[2], "--committed" in sys.argv[3:])
    if mode == "--parse":  # a migration file's text on stdin -> its changes
        print(json.dumps(sorted(migration_changes(sys.stdin.read()))))
        return 0
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    try:
        if mode == "--record":
            return record_question(event)
        if mode == "--prompt":
            return record_prompt(event)
        return pre_tool_use(event)
    except Exception:  # noqa: BLE001 - a guard that crashes must never block the session
        return 0


if __name__ == "__main__":
    sys.exit(main())
