# agents

One home for everything I want every coding agent to share: instructions, skills, hooks, MCP servers.

| What | Where | Claude Code | Codex | Gemini CLI |
|---|---|---|---|---|
| Instructions | `AGENTS.md` | `~/.claude/CLAUDE.md` imports it | `~/.codex/AGENTS.md` symlink | `~/.gemini/AGENTS.md` + `context.fileName` |
| Skills | `skills/` (open Agent Skills format) | `~/.claude/skills` symlink | reads `~/.agents/skills` natively | `~/.gemini/skills` symlink |
| Hooks | `hooks/*.sh` (same scripts everywhere) | `claude/apply.py` merges `claude/settings.hooks.json` | `~/.codex/hooks.json` symlink | n/a |
| Permission rules | `codex/rules/default.rules`, `claude/permissions.json` | `claude/apply.py` merges | `~/.codex/rules` symlink | n/a |
| MCP servers | `mcp/servers.json` + gitignored `mcp/secrets.env` | `mcp/render.py claude --apply` | `mcp/render.py codex --apply` | `mcp/render.py gemini` |

Fresh machine: clone to `~/.agents`, copy `mcp/secrets.env.example` to `mcp/secrets.env` and fill it, run `./install.sh`.

For Codex only, run `./install.sh codex`. Python 3.11+ is required by the configuration scripts. The installer respects an existing `CODEX_HOME` and preserves unrelated settings and existing links/files. Conflicting links are reported rather than replaced.

## Codex setup

`codex/apply.py` adds `CLAUDE.md` as a fallback project instruction filename; `AGENTS.override.md` and `AGENTS.md` still take precedence. The default project instruction budget here is 128 KiB because the backend's `CLAUDE.md` exceeds Codex's 32 KiB default. Existing custom filenames and an explicitly configured budget are preserved. See the [official instruction discovery documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

Skills are discovered natively in `~/.agents/skills`; the obsolete `features.skills` entry is removed when applying the setup. Shared skills' `$ARGUMENTS` placeholders mean the arguments supplied by the user, including when invoked as `$skill-name` in Codex. See [local skill discovery](https://learn.chatgpt.com/docs/build-skills).

Current Codex supports the shared `UserPromptSubmit` hooks. Both shell wrappers read their sections from `AGENTS.md` through `hooks/reminder.py`, so editing the instructions updates the per-turn reminder too. The voice hook explicitly sends normal-prose instructions while `.brainrot-off` exists. Hook definitions are in `codex/hooks.json`; use `/hooks` to review their trust status if Codex reports a new or changed definition. The installer does not grant hook trust or bypass approval settings. See [Codex hooks](https://learn.chatgpt.com/docs/hooks).

Validated with Codex CLI 0.153.4: instruction/skill/rule links, hook JSON, voice toggle, repeated config application, and project instruction discovery. Restart Codex after configuration changes to rebuild the initial instruction chain. Run the local checks with `python3 -m unittest discover -s tests`.

Not portable: claude.ai connectors (Slack, Linear, Notion OAuth through claude.ai), Claude plugins (Datadog), and Claude's auto-memory (permission allowlists are versioned but merged per machine). Those stay per tool.
