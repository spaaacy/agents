# agents

One home for everything I want every coding agent to share: instructions, skills, hooks, MCP servers.

| What | Where | Claude Code | Codex | Gemini CLI |
|---|---|---|---|---|
| Instructions | `AGENTS.md` | `~/.claude/CLAUDE.md` imports it | `~/.codex/AGENTS.md` symlink | `~/.gemini/AGENTS.md` + `context.fileName` |
| Skills | `skills/` (open Agent Skills format) | `~/.claude/skills` symlink | reads `~/.agents/skills` natively | `~/.gemini/skills` symlink |
| Hooks | `hooks/<tool>/` | `claude/apply.py` merges `claude/settings.hooks.json` | n/a (voice via AGENTS.md) | n/a |
| Permission rules | `codex/default.rules`, `claude/permissions.json` | `claude/apply.py` merges | `~/.codex/rules` symlink | n/a |
| MCP servers | `mcp/servers.json` + gitignored `mcp/secrets.env` | `mcp/render.py claude --apply` | `mcp/render.py codex --apply` | `mcp/render.py gemini` |

Fresh machine: clone to `~/.agents`, copy `mcp/secrets.env.example` to `mcp/secrets.env` and fill it, run `./install.sh`.

Not portable: claude.ai connectors (Slack, Linear, Notion OAuth through claude.ai), Claude plugins (Datadog), and Claude's auto-memory (permission allowlists are versioned but merged per machine). Those stay per tool.
