#!/usr/bin/env python3
"""Which agent the background tools run on, Codex or Claude, and the locked-down Codex setup they share.

Every plugin of the agents hub (~/.agents/hub) reads the choice before every run, so switching takes effect on the next request with
no restart:

  agents-engine                       print the current choice
  agents-engine codex                 switch all three to Codex (model and effort kept)
  agents-engine codex --model gpt-6.1-sol --effort medium
  agents-engine claude                switch all three to Claude

The choice lives in ~/.agents/state/engine.json (not in git). With no file the
tools run on Codex, gpt-6.1-sol at medium effort.

Codex runs unattended from its own home, ~/.agents/state/codex-home, rebuilt
before each run: it shares the login (auth.json) and the session store with
~/.codex, so `codex resume <id>` works from the laptop, but none of the
interactive config. In that home every file is read-only, the network is on
(gh and Datadog need it), nothing is ever approved, and background.rules
refuses the commands that write somewhere else (git push, gh api, PR and issue
writes, az). The interactive config's own rules, which allow git push and
commit, are not loaded there.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import tomllib
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STATE = Path.home() / ".agents" / "state"
CHOICE_FILE = STATE / "engine.json"
CODEX_HOME = STATE / "codex-home"
USER_CODEX_HOME = Path(os.environ.get("CODEX_HOME_INTERACTIVE", Path.home() / ".codex"))
RULES_FILE = HERE / "background.rules"
CODEX = os.environ.get("CODEX_BIN", str(Path.home() / ".local" / "bin" / "codex"))
CLAUDE = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local" / "bin" / "claude"))

ENGINES = ("codex", "claude")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT: dict[str, Any] = {
    "engine": "codex",
    "codex": {"model": "gpt-6.1-sol", "effort": "medium"},
    "claude": {"model": None},
}

# Linear's read tools; its write tools stay off in unattended runs.
LINEAR_READ_TOOLS = [
    "get_issue", "list_issues", "list_comments", "get_project", "list_projects", "get_document",
    "list_documents", "get_milestone", "list_milestones", "list_cycles", "get_user", "list_users",
    "get_team", "list_teams", "list_issue_statuses", "list_issue_labels", "get_attachment",
    "extract_images", "get_status_updates",
]
# MCP tools an unattended run may call without asking (nobody is there to approve one). All are reads.
APPROVED_MCP_TOOLS = {
    "pg-readonly": ["connection_info", "describe_table", "find_entity", "list_tables", "run_sql",
                    "schema_overview", "search_columns"],
    "context7": ["resolve-library-id", "query-docs"],
    "linear": LINEAR_READ_TOOLS,
}
# Servers whose every tool is a read, approved whole: Datadog's MCP write access is off for the org.
READ_ONLY_MCP_SERVERS = {"datadog"}
# Files no unattended run needs to read, hidden even from reads.
HIDDEN_PATHS = [Path.home() / ".ssh"]


# --------------------------------------------------------------------------- the choice


def current(path: Path | None = None) -> dict[str, Any]:
    """The engine choice, with defaults filled in. An unreadable or unknown choice reads as the default."""
    path = path or CHOICE_FILE
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        saved = {}
    choice = json.loads(json.dumps(DEFAULT))
    if saved.get("engine") in ENGINES:
        choice["engine"] = saved["engine"]
    for engine in ENGINES:
        if isinstance(saved.get(engine), dict):
            choice[engine].update({k: v for k, v in saved[engine].items() if k in choice[engine]})
    if choice["codex"]["effort"] not in EFFORTS:
        choice["codex"]["effort"] = DEFAULT["codex"]["effort"]
    return choice


def engine(path: Path | None = None) -> str:
    return current(path)["engine"]


def choose(name: str, model: str | None = None, effort: str | None = None, path: Path | None = None) -> dict[str, Any]:
    """Switch every background tool to ``name``; a model or effort given is kept for later switches too."""
    path = path or CHOICE_FILE
    if name not in ENGINES:
        raise ValueError(f"unknown engine {name!r}; pick one of {', '.join(ENGINES)}")
    if effort is not None and (name != "codex" or effort not in EFFORTS):
        raise ValueError(f"effort is a Codex setting, one of {', '.join(EFFORTS)}")
    choice = current(path)
    choice["engine"] = name
    if model:
        choice[name]["model"] = model
    if effort:
        choice["codex"]["effort"] = effort
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(choice, indent=1) + "\n")
    tmp.replace(path)
    return choice


def describe(choice: dict[str, Any] | None = None) -> str:
    choice = choice or current()
    if choice["engine"] == "codex":
        return f"codex ({choice['codex']['model']}, {choice['codex']['effort']} effort)"
    model = choice["claude"].get("model")
    return f"claude ({model})" if model else "claude (its default model)"


def claude_model_args(choice: dict[str, Any] | None = None) -> list[str]:
    """``--model`` for a claude run when a Claude model was chosen, else nothing."""
    model = (choice or current())["claude"].get("model")
    return ["--model", model] if model else []


# --------------------------------------------------------------------------- the Codex home


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {_toml_value(v)}" for k, v in value.items()) + " }"
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def _table(name: str, values: dict[str, Any]) -> str:
    lines = [f"[{name}]"] + [f"{json.dumps(k)} = {_toml_value(v)}" for k, v in values.items()]
    return "\n".join(lines) + "\n"


def mcp_servers(names: list[str], user_config: Path | None = None) -> dict[str, dict[str, Any]]:
    """The named MCP servers from the interactive Codex config, read-only where they can write."""
    path = user_config or USER_CODEX_HOME / "config.toml"
    try:
        found = tomllib.loads(path.read_text()).get("mcp_servers", {})
    except (OSError, tomllib.TOMLDecodeError):
        found = {}
    servers: dict[str, dict[str, Any]] = {}
    for name in names:
        server = found.get(name)
        if not isinstance(server, dict):
            continue
        # Per-tool approvals (Linear's save_issue is approved interactively) never carry over.
        server = {k: v for k, v in server.items() if k != "tools"}
        if name == "linear":
            server["enabled_tools"] = LINEAR_READ_TOOLS
        if name in APPROVED_MCP_TOOLS:
            server["tools"] = {tool: {"approval_mode": "approve"} for tool in APPROVED_MCP_TOOLS[name]}
        if name in READ_ONLY_MCP_SERVERS:
            server["default_tools_approval_mode"] = "approve"
        servers[name] = server
    return servers


def codex_config(choice: dict[str, Any], servers: dict[str, dict[str, Any]]) -> str:
    """config.toml for the unattended Codex home."""
    top = {
        "model": choice["codex"]["model"],
        "model_reasoning_effort": choice["codex"]["effort"],
        # Nobody is there to approve anything; whatever would need approval is refused.
        "approval_policy": "never",
        "default_permissions": "background",
        "project_doc_max_bytes": 131072,
        "project_doc_fallback_filenames": ["CLAUDE.md"],
    }
    parts = [
        "# Written by ~/.agents/engine/engine.py before every unattended run. Edits here are lost.\n",
        "\n".join(f"{k} = {_toml_value(v)}" for k, v in top.items()) + "\n",
        # The runners hand some runs keys (on-call's Datadog keys); keep them in the commands' environment.
        _table("shell_environment_policy", {"inherit": "all", "ignore_default_excludes": True}),
        _table("permissions.background.filesystem",
               {":root": "read", **{str(p): "none" for p in HIDDEN_PATHS}}),
        _table("permissions.background.network", {"enabled": True}),
    ]
    for name, server in servers.items():
        nested = {k: v for k, v in server.items() if isinstance(v, dict)}
        flat = {k: v for k, v in server.items() if not isinstance(v, dict)}
        parts.append(_table(f"mcp_servers.{json.dumps(name)}", flat))
        for key, value in nested.items():
            if key == "tools":
                for tool, settings in value.items():
                    parts.append(_table(f"mcp_servers.{json.dumps(name)}.tools.{json.dumps(tool)}", settings))
            else:
                parts.append(_table(f"mcp_servers.{json.dumps(name)}.{key}", value))
    return "\n".join(parts)


def _link(target: Path, link: Path) -> None:
    if link.is_symlink() and Path(os.readlink(link)) == target:
        return
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)
    link.symlink_to(target)


def prepare_codex_home(mcp_names: list[str] | None = None, home: Path = CODEX_HOME,
                       choice: dict[str, Any] | None = None) -> Path:
    """Build (or refresh) the unattended Codex home and return it, for CODEX_HOME."""
    choice = choice or current()
    home.mkdir(parents=True, exist_ok=True)
    (home / "rules").mkdir(exist_ok=True)
    (USER_CODEX_HOME / "sessions").mkdir(parents=True, exist_ok=True)
    _link(USER_CODEX_HOME / "auth.json", home / "auth.json")
    # One session store, so `codex resume <id>` on the laptop finds these runs.
    _link(USER_CODEX_HOME / "sessions", home / "sessions")
    for stale in (home / "rules").glob("*.rules"):
        if stale.name != RULES_FILE.name:
            stale.unlink()
    shutil.copyfile(RULES_FILE, home / "rules" / RULES_FILE.name)
    config = home / "config.toml"
    # Two runs can prepare the home at once; each writes its own temporary file.
    tmp = config.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(codex_config(choice, mcp_servers(mcp_names or [])))
    tmp.chmod(0o600)  # it carries MCP keys copied from the interactive config
    tmp.replace(config)
    return home


def codex_env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {**os.environ, **(extra or {}), "CODEX_HOME": str(home)}
    return env


def codex_command(cwd: Path, output: Path, *, session: str | None = None,
                  schema: Path | None = None) -> list[str]:
    """One unattended ``codex exec`` turn (a new session, or ``session`` resumed); the prompt goes on stdin.

    Model, effort, sandbox and approvals come from the home's config.toml, so a
    resumed turn gets the same limits as the first.
    """
    cmd = [CODEX, "exec"]
    if session:
        cmd += ["resume"]
    cmd += ["--json", "--skip-git-repo-check", "-o", str(output)]
    if not session:
        # `exec resume` takes neither: a resumed session keeps its first turn's folder.
        cmd += ["--color", "never", "-C", str(cwd)]
    if schema is not None:
        cmd += ["--output-schema", str(schema)]
    if session:
        cmd += [session]
    return cmd + ["-"]


class CodexEvents:
    """Collects what a ``codex exec --json`` run reports: its session, its last message, token use, errors."""

    def __init__(self) -> None:
        self.session: str | None = None
        self.last_message = ""
        self.usage: dict[str, Any] = {}
        self.error: str | None = None
        self.refused = 0

    def feed(self, line: str) -> dict[str, Any] | None:
        try:
            event = json.loads(line)
        except ValueError:
            return None
        if not isinstance(event, dict):
            return None
        kind = event.get("type")
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if kind == "thread.started":
            self.session = event.get("thread_id") or self.session
        elif kind == "item.completed" and item.get("type") == "agent_message":
            self.last_message = str(item.get("text") or "")
        elif kind == "item.completed" and item.get("type") == "command_execution":
            if "rejected" in str(item.get("aggregated_output") or "").lower() and item.get("exit_code") is None:
                self.refused += 1
        elif kind == "turn.completed":
            self.usage = event.get("usage") or {}
        elif kind in ("turn.failed", "error"):
            detail = event.get("error") if isinstance(event.get("error"), dict) else event
            self.error = str(detail.get("message") or detail)[:500]
        return event

    def tokens(self) -> int | None:
        if not self.usage:
            return None
        return int(self.usage.get("input_tokens") or 0) + int(self.usage.get("output_tokens") or 0)


def final_text(output: Path, events: CodexEvents) -> str:
    try:
        text = output.read_text().strip()
    except OSError:
        text = ""
    return text or events.last_message.strip()


# --------------------------------------------------------------------------- the command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agents-engine", description="Pick Codex or Claude for the background tools.")
    parser.add_argument("engine", nargs="?", choices=ENGINES, help="switch to this engine; omit to print the choice")
    parser.add_argument("--model", help="model for that engine (kept for later switches)")
    parser.add_argument("--effort", choices=EFFORTS, help="Codex reasoning effort (kept for later switches)")
    args = parser.parse_args(argv)
    if args.engine is None:
        if args.model or args.effort:
            parser.error("name the engine the model or effort is for")
        print(f"background agents run on {describe()}")
        return 0
    try:
        choice = choose(args.engine, args.model, args.effort)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"background agents now run on {describe(choice)}; the next request uses it, no restart needed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
