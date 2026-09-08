# agents

One home for everything I want every coding agent to share: instructions, skills, hooks, MCP servers.

| What | Where | Claude Code | Codex | Gemini CLI |
|---|---|---|---|---|
| Instructions | `AGENTS.md` | `~/.claude/CLAUDE.md` imports it | `~/.codex/AGENTS.md` symlink | `~/.gemini/AGENTS.md` + `context.fileName` |
| Skills | `skills/` (open Agent Skills format) | `~/.claude/skills` symlink | reads `~/.agents/skills` natively | `~/.gemini/skills` symlink |
| Hooks | `hooks/<tool>/` | `claude/settings.hooks.json` snippet | n/a | n/a |
| MCP servers | `mcp/servers.json` + gitignored `mcp/secrets.env` | `mcp/render.py claude \| sh` | `mcp/render.py codex` | `mcp/render.py gemini` |

Fresh machine: clone to `~/.agents`, copy `mcp/secrets.env.example` to `mcp/secrets.env` and fill it, run `./install.sh`.

Not portable: claude.ai connectors (Slack, Linear, Notion OAuth through claude.ai), Claude plugins (Datadog), tool permission allowlists, and Claude's auto-memory. Those stay per tool.
