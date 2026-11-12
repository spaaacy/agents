"""The one place an agent turn is run, on Codex or Claude, for every plugin.

A plugin describes the turn as an AgentTask (prompt, instructions, the session
to resume, the folder, how locked down) and gets an AgentResult back. Which
agent runs it is the engine switch's choice (`agents engine`), read per turn.

Two modes:
- "read-only" (the default): Codex from the locked-down home (engine.py);
  Claude with no saved settings, the plugin's deny-list settings file, dontAsk
  and the plugin's allow list.
- "review": a throwaway checkout the agent may write to and run tests in.
  Codex runs with its normal protections and automatic approval review in
  that folder; Claude the same way as read-only (its allow list decides).

Sessions are stored tagged with their engine ("codex:<id>", "claude:<id>"; a
bare id is an old Claude one), so a thread one agent started is never handed
to the other.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import engine

CLAUDE_JSON = Path.home() / ".claude.json"
LOST_SESSION = ("no conversation found", "no rollout found", "session not found")
ENGINES = engine.ENGINES


@dataclass
class AgentTask:
    prompt: str
    cwd: Path
    system: str = ""                      # instructions: Claude's appended system prompt, Codex's preamble
    session: str | None = None            # tagged session id to resume
    schema: dict[str, Any] | None = None  # a structured final answer
    mode: str = "read-only"               # "read-only", "review" (a throwaway checkout) or "act" (an approved action)
    timeout: int = 900
    run_dir: Path | None = None           # events.jsonl and scratch files go here
    env: dict[str, str] = field(default_factory=dict)  # extra environment for the agent's commands
    mcp_servers: list[str] = field(default_factory=list)
    add_dirs: list[str] = field(default_factory=list)
    claude_allowed_tools: list[str] = field(default_factory=list)
    claude_settings: Path | None = None
    engine: str | None = None             # pin an engine; default is the switch's choice
    write_dirs: list[str] = field(default_factory=list)  # act mode: more folders it may write in
    repos: list[str] = field(default_factory=list)        # act mode: checkouts whose git data it may write


@dataclass
class AgentResult:
    engine: str
    text: str = ""
    session: str | None = None            # tagged
    error: str | None = None
    structured: dict[str, Any] | None = None
    tokens: int | None = None
    cost_usd: float | None = None
    refused: int = 0

    @property
    def lost_session(self) -> bool:
        return bool(self.error) and any(gone in self.error.lower() for gone in LOST_SESSION)

    def resume_command(self, cwd: str = "~/dev/envoyai-backend") -> str | None:
        if not self.session:
            return None
        name, sid = split_session(self.session)
        return f"cd {cwd} && codex resume {sid}" if name == "codex" else f"cd {cwd} && claude --resume {sid}"


def tag(name: str, session_id: str | None) -> str | None:
    return f"{name}:{session_id}" if session_id else None


def split_session(stored: str) -> tuple[str, str]:
    """(engine, id) of a stored session; a bare id is an old Claude session."""
    for name in ENGINES:
        if stored.startswith(name + ":"):
            return name, stored[len(name) + 1 :]
    return "claude", stored


def resumable(stored: str | None, name: str | None = None) -> str | None:
    """The tagged session when the engine about to run can resume it, else None (start fresh)."""
    if not stored:
        return None
    return stored if split_session(stored)[0] == (name or engine.engine()) else None


# --------------------------------------------------------------------------- running a process

LineHandler = Callable[[str], None]


def stream(cmd: list[str], stdin: str, cwd: Path, timeout: int, on_line: LineHandler,
           env: dict[str, str] | None = None, cancel: threading.Event | None = None) -> tuple[int, str]:
    """Run a command, hand each stdout line over as it arrives; (exit code, stderr).

    Raises subprocess.TimeoutExpired after a run outlives ``timeout``, and
    InterruptedError when ``cancel`` is set; the process is killed either way.
    """
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=cwd, env=env, start_new_session=True)
    timed_out = threading.Event()
    done = threading.Event()

    def kill() -> None:
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError):
            proc.kill()

    def on_timeout() -> None:
        timed_out.set()
        kill()

    def watch_cancel() -> None:
        while cancel is not None and not done.is_set():
            if cancel.wait(0.5):
                kill()
                return

    timer = threading.Timer(timeout, on_timeout)
    timer.daemon = True
    timer.start()
    if cancel is not None:
        threading.Thread(target=watch_cancel, daemon=True).start()
    stderr: list[str] = []
    reader = threading.Thread(target=lambda: stderr.append(proc.stderr.read() if proc.stderr else ""), daemon=True)
    reader.start()
    try:
        if proc.stdin:
            try:
                proc.stdin.write(stdin)
                proc.stdin.close()
            except BrokenPipeError:
                pass
        for line in proc.stdout or []:
            on_line(line)
        code = proc.wait()
    finally:
        done.set()
        timer.cancel()
    reader.join(timeout=5)
    if cancel is not None and cancel.is_set():
        raise InterruptedError("cancelled")
    if timed_out.is_set():
        raise subprocess.TimeoutExpired(cmd, timeout)
    return code, "".join(stderr)


Execute = Callable[..., tuple[int, str]]


# --------------------------------------------------------------------------- Claude


def claude_mcp_config(names: list[str], claude_json: Path = CLAUDE_JSON) -> dict[str, Any]:
    """The named MCP servers from the user's Claude config (saved settings are not loaded for these runs)."""
    try:
        found = json.loads(claude_json.read_text()).get("mcpServers", {})
    except (OSError, ValueError):
        found = {}
    return {"mcpServers": {name: found[name] for name in names if name in found}}


def claude_command(task: AgentTask, session_id: str | None, mcp_path: Path | None) -> list[str]:
    cmd = [engine.CLAUDE, "-p", "--output-format", "stream-json", "--verbose",
           # No saved settings: they allow pushes, commits and ticket edits.
           "--setting-sources", "", "--permission-mode", "dontAsk"]
    if task.claude_settings:
        cmd += ["--settings", str(task.claude_settings)]
    if task.system:
        cmd += ["--append-system-prompt", task.system]
    for directory in task.add_dirs:
        cmd += ["--add-dir", str(Path(directory).expanduser())]
    if mcp_path is not None:
        cmd += ["--mcp-config", str(mcp_path)]
    if session_id:
        cmd += ["--resume", session_id]
    if task.schema is not None:
        cmd += ["--json-schema", json.dumps(task.schema)]
    cmd += engine.claude_model_args()
    # Takes every following argument, so it stays last.
    return cmd + ["--allowedTools", *task.claude_allowed_tools]


def run_claude(task: AgentTask, cancel: threading.Event | None = None, execute: Execute = stream) -> AgentResult:
    session_id = split_session(task.session)[1] if task.session else None
    run_dir = task.run_dir or Path.cwd()
    run_dir.mkdir(parents=True, exist_ok=True)
    servers = claude_mcp_config(task.mcp_servers)
    mcp_path = None
    if servers["mcpServers"]:
        mcp_path = run_dir / "claude-mcp.json"
        mcp_path.write_text(json.dumps(servers))
        mcp_path.chmod(0o600)
    final: dict[str, Any] = {}
    events = (run_dir / "events.jsonl").open("a")

    def on_line(line: str) -> None:
        events.write(line if line.endswith("\n") else line + "\n")
        events.flush()
        try:
            event = json.loads(line)
        except ValueError:
            return
        if isinstance(event, dict) and event.get("type") == "result":
            final.update(event)

    try:
        git_env = engine.act_git_env() if task.mode == "act" else {}
        code, stderr = execute(claude_command(task, session_id, mcp_path), task.prompt, task.cwd, task.timeout,
                               on_line, {**os.environ, **git_env, **task.env}, cancel)
    except subprocess.TimeoutExpired:
        return AgentResult("claude", session=task.session, error=f"Claude did not finish within {task.timeout} seconds")
    except InterruptedError:
        return AgentResult("claude", session=task.session, error="cancelled")
    finally:
        events.close()
        if mcp_path:
            mcp_path.unlink(missing_ok=True)
    if not final:
        tail = " | ".join(stderr.strip().splitlines()[-3:])
        return AgentResult("claude", session=task.session, error=f"claude exited {code}: {tail or 'no output'}")
    session = tag("claude", final.get("session_id")) or task.session
    usage = final.get("usage") or {}
    tokens = (int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)) or None
    result = AgentResult("claude", session=session, cost_usd=final.get("total_cost_usd"), tokens=tokens,
                         refused=len(final.get("permission_denials") or []))
    if final.get("is_error") or code != 0:
        result.error = str(final.get("result") or "Claude reported an error")[:500]
        return result
    result.text = str(final.get("result") or "").strip()
    if isinstance(final.get("structured_output"), dict):
        result.structured = final["structured_output"]
    return result


# --------------------------------------------------------------------------- Codex


def codex_review_command(task: AgentTask, output: Path, schema: Path | None, session_id: str | None) -> list[str]:
    """Codex in a throwaway checkout: normal protections, automatic approval review, network on."""
    choice = engine.current()["codex"]
    cmd = [engine.CODEX, "exec", "--approve-for-me", "--json", "--color", "never",
           "-m", choice["model"], "-c", f'model_reasoning_effort="{choice["effort"]}"',
           "-c", "sandbox_workspace_write.network_access=true"]
    if session_id:
        cmd += ["resume"]
    else:
        cmd += ["-C", str(task.cwd)]
    if schema is not None:
        cmd += ["--output-schema", str(schema)]
    cmd += ["-o", str(output)]
    if session_id:
        cmd += [session_id]
    return cmd + ["-"]


def run_codex(task: AgentTask, cancel: threading.Event | None = None, execute: Execute = stream) -> AgentResult:
    session_id = split_session(task.session)[1] if task.session else None
    run_dir = task.run_dir or Path.cwd()
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / f"codex-answer-{threading.get_ident()}.txt"
    output.unlink(missing_ok=True)
    schema_path = None
    if task.schema is not None:
        schema_path = run_dir / "schema.json"
        schema_path.write_text(json.dumps(task.schema))
    # Codex has no system-prompt flag: a new session gets the instructions in front of the request.
    prompt = task.prompt if session_id or not task.system else f"{task.system}\n\n---\n\n{task.prompt}"
    if task.mode == "review":
        cmd = codex_review_command(task, output, schema_path, session_id)
        env = {**os.environ, **task.env}
    elif task.mode == "act":
        writable = engine.default_write_dirs(task.repos) + [run_dir] + [Path(d).expanduser() for d in task.write_dirs]
        home = engine.prepare_codex_home(task.mcp_servers, act=True, write_dirs=writable)
        cmd = engine.codex_command(task.cwd, output, session=session_id, schema=schema_path)
        env = engine.codex_env(home, {**engine.act_git_env(), **task.env})
    else:
        home = engine.prepare_codex_home(task.mcp_servers)
        cmd = engine.codex_command(task.cwd, output, session=session_id, schema=schema_path)
        env = engine.codex_env(home, task.env)
    seen = engine.CodexEvents()
    events = (run_dir / "events.jsonl").open("a")

    def on_line(line: str) -> None:
        events.write(line if line.endswith("\n") else line + "\n")
        events.flush()
        seen.feed(line)

    try:
        code, stderr = execute(cmd, prompt, task.cwd, task.timeout, on_line, env, cancel)
    except subprocess.TimeoutExpired:
        return AgentResult("codex", session=tag("codex", seen.session) or task.session,
                           error=f"Codex did not finish within {task.timeout} seconds")
    except InterruptedError:
        return AgentResult("codex", session=tag("codex", seen.session) or task.session, error="cancelled")
    finally:
        events.close()
    text = engine.final_text(output, seen)
    output.unlink(missing_ok=True)
    result = AgentResult("codex", session=tag("codex", seen.session) or task.session, tokens=seen.tokens(),
                         refused=seen.refused)
    if code != 0 or seen.error or not text:
        tail = " | ".join(stderr.strip().splitlines()[-3:])
        detail = seen.error or tail or "no output"
        result.error = f"codex exited {code}: {detail}" if code else detail
        return result
    result.text = text
    if task.schema is not None:
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            result.structured = parsed
    return result


RUNNERS: dict[str, Callable[..., AgentResult]] = {"claude": run_claude, "codex": run_codex}

# An approved action on Claude: its deny list (act-claude-settings.json) still refuses merging, approving,
# force-pushing and az; this allow list lets it edit, commit, push and open PRs, and write Linear tickets.
ACT_CLAUDE_SETTINGS = Path(__file__).resolve().parent / "act-claude-settings.json"
ACT_CLAUDE_TOOLS = [
    "Read", "Grep", "Glob", "Edit", "Write", "MultiEdit",
    "Bash(git *)", "Bash(gh pr view *)", "Bash(gh pr diff *)", "Bash(gh pr list *)", "Bash(gh pr checks *)",
    "Bash(gh pr create *)", "Bash(gh pr edit *)", "Bash(gh pr comment *)", "Bash(gh run view *)", "Bash(gh run list *)",
    "Bash(gh issue view *)", "Bash(gh search *)", "Bash(python3 *)", "Bash(poetry run *)",
    "Bash(ENVIRONMENT=test poetry run *)", "Bash(npx *)", "Bash(npm run *)", "Bash(rg *)", "Bash(ls *)",
    "Bash(cat *)", "Bash(head *)", "Bash(tail *)", "Bash(wc *)", "Bash(grep *)", "Bash(mkdir *)",
    "mcp__claude_ai_Linear", "mcp__pg-readonly", "mcp__context7", "mcp__plugin_datadog_mcp",
]
ACT_PREAMBLE = """Aakif tapped a button in Slack approving exactly the action below, and nothing more. Nobody is watching this run and nobody can answer a question, so do the action, then report.
- Work in a worktree under ~/dev/.claude/worktrees (create one from origin when you need to change a repo); main checkouts are not writable here. Follow ~/.agents/AGENTS.md and the repo's CLAUDE.md.
- You may commit, push a branch and open a draft PR, and create or update Linear tickets and comments when the action calls for it (a ticket is assigned to Aakif and follows AGENTS.md's ticket rules).
- Never merge, approve, close a PR, force-push, delete a branch, run az, or do anything the action did not ask for. If the action turns out to need one of those, or a product decision, stop and say so.
- Your last message is posted back in Slack as it is: what you did, with links (PR, ticket), in a few lines.

The action:
"""


def run(task: AgentTask, cancel: threading.Event | None = None) -> AgentResult:
    """Run one turn on the pinned engine, or the switch's current choice."""
    name = task.engine or engine.engine()
    if task.session and split_session(task.session)[0] != name:
        task = AgentTask(**{**task.__dict__, "session": None})
    return RUNNERS[name](task, cancel)
