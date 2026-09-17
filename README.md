# agents

One home for everything I want every coding agent to share: instructions, skills, hooks, MCP servers.

| What | Where | Claude Code | Codex | Gemini CLI |
|---|---|---|---|---|
| Instructions | `AGENTS.md` | `~/.claude/CLAUDE.md` imports it | `~/.codex/AGENTS.md` symlink | `~/.gemini/AGENTS.md` + `context.fileName` |
| Skills | `skills/` (open Agent Skills format) | `~/.claude/skills` symlink | reads `~/.agents/skills` natively | `~/.gemini/skills` symlink |
| Hooks | `hooks/*.sh` (same scripts everywhere) | `claude/apply.py` merges `claude/settings.hooks.json` | `~/.codex/hooks.json` symlink | n/a |
| Permission rules | `codex/rules/default.rules`, `claude/permissions.json` | `claude/apply.py` merges | `~/.codex/rules` symlink | n/a |
| Session titles | `hooks/session_title.py` (`dev: <ticket\|#PR>` from the branch at start, `review: <#PR\|branch>` on `/review`) | `claude/apply.py` merges the SessionStart + UserPromptSubmit hooks | n/a (no session titles) | n/a |
| MCP servers | `mcp/servers.json` + gitignored `mcp/secrets.env` | `mcp/render.py claude --apply` | `mcp/render.py codex --apply` | `mcp/render.py gemini` |

Fresh machine: clone to `~/.agents`, copy `mcp/secrets.env.example` to `mcp/secrets.env` and fill it, run `./install.sh`.

For Codex only, run `./install.sh codex`. Python 3.11+ is required by the configuration scripts. The installer respects an existing `CODEX_HOME` and preserves unrelated settings and existing links/files. Conflicting links are reported rather than replaced.

## Account connections

Installing local files does not install a hosted plugin, sign into its account, or add tools to an existing chat. `codex/connections.json` declares services routed through Codex plugins (currently Slack); the renderer explicitly reports that routing without claiming the connection is ready.

The installer finishes by running `python3 ~/.agents/codex/check_connections.py`. Run it independently whenever a connection is missing. It launches Codex's app server and uses the read-only `app/installed` check with a fresh runtime snapshot, requiring both `enabled` and `callable`. It does not start a model turn, read messages, install plugins, sign in, change permissions, or read credential files. Codex itself handles its normal authentication and local runtime cache. See the [official app runtime check](https://learn.chatgpt.com/docs/app-server#apps-connectors).

The check reports `TOOLS AVAILABLE` only after observing callable tools, `ACTION REQUIRED` for missing/disabled connections, and `UNVERIFIED` for timeouts, unsupported commands, or other check failures. Exit codes are 0, 1, and 2 respectively; an incomplete check makes installation exit nonzero even though local files were installed. The default timeout is 30 seconds; override with `--timeout SECONDS`. Existing direct server configurations are preserved and checked for a connected runtime with tools; saved credentials alone do not pass.

For Slack account setup, open the app's **Plugins** tab, find **Slack**, install it if missing, and complete its account connection. From terminal Codex, run `codex`, then `/plugins`. Start a new chat after installation, then rerun the check. The checker cannot prove access to a specific private message or make tools appear in an already-open chat. Other direct server connections are outside this check's scope. See the [official plugin setup steps](https://learn.chatgpt.com/docs/plugins#install-and-use-a-plugin).

## Codex setup

`codex/apply.py` adds `CLAUDE.md` as a fallback project instruction filename; `AGENTS.override.md` and `AGENTS.md` still take precedence. The default project instruction budget here is 128 KiB because the backend's `CLAUDE.md` exceeds Codex's 32 KiB default. Existing custom filenames and an explicitly configured budget are preserved. See the [official instruction discovery documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

Skills are discovered natively in `~/.agents/skills`; the obsolete `features.skills` entry is removed when applying the setup. Shared skills' `$ARGUMENTS` placeholders mean the arguments supplied by the user, including when invoked as `$skill-name` in Codex. See [local skill discovery](https://learn.chatgpt.com/docs/build-skills).

Current Codex supports the shared `UserPromptSubmit` hooks. Both shell wrappers read their sections from `AGENTS.md` through `hooks/reminder.py`, so editing the instructions updates the per-turn reminder too. The voice hook explicitly sends normal-prose instructions while `.brainrot-off` exists. Hook definitions are in `codex/hooks.json`; use `/hooks` to review their trust status if Codex reports a new or changed definition. The installer does not grant hook trust or bypass approval settings. See [Codex hooks](https://learn.chatgpt.com/docs/hooks).

Validated with Codex CLI 0.153.4: instruction/skill/rule links, hook JSON, voice toggle, repeated config application, and project instruction discovery. Restart Codex after configuration changes to rebuild the initial instruction chain. Run the local checks with `python3 -m unittest discover -s tests`.

Not portable: claude.ai connectors (Slack, Linear, Notion OAuth through claude.ai), Claude plugins (Datadog), and Claude's auto-memory (permission allowlists are versioned but merged per machine). Those stay per tool.
