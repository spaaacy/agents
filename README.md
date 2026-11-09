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

## Skill flow

The skills under `skills/` are one lifecycle, and each one names its neighbours in a "Flow" line near its top so an agent running one knows what came before and what comes next.

```
/pickup <ticket>        context, then on go: worktree, test-first build, draft PR
  /review <pr>          the author's own pre-PR review (read-only)
    /post-review        posts it as one GitHub review; /review asks whether to run it
  /impact <pr>          what a PR delivers for carriers, reps and admins, no code (read-only)
  /fix <pr|ticket>      keeps PRs level with development and CI green, triages red evals; never merges
  reviewers comment
  /address-review <pr>  verify the findings, then on go: fix, reply, push
  /verify-fixes <pr>    fresh-eyes check of that round; routes back, and from round 3 closes the review
  the user merges
/linear-sweep           tickets to the state their code actually reached
/cleanup                worktrees, local databases, migration drift; ends with the local stack up and migrated
```

Shared rules every skill follows: a skill reads and reports before it acts, then carries on down the recommended path without asking for a go; it stops only for the user's real calls (product calls, standing eval reds, a conflict it will not guess, a test pinning old behaviour, deletions, an ambiguous target, a dirty worktree, and `/pickup`'s go to start building); creating a Linear ticket or changing one (assignee, title, description, labels, priority, project, state) always needs explicit approval, with `/linear-sweep`'s state moves on the user's own tickets the one exception, while posting a comment never does; nothing merges, approves, resolves threads, or runs `az`; work happens in a worktree on the PR branch (`~/dev/.claude/worktrees/<slug>`), never on a main checkout's branch, never with `git stash`; anything posted to GitHub, Linear or Slack is plain professional English whatever the chat voice; standing eval reds are added only with the user's explicit words; and every decision, go, or post is asked through the client's structured question tool (Claude Code: `AskUserQuestion`), one call carrying every open decision with the recommended option first, never a "reply go" line the user has to answer in free text.

## Account connections

Installing local files does not install a hosted plugin, sign into its account, or add tools to an existing chat. `codex/connections.json` declares services routed through Codex plugins (currently Slack); the renderer explicitly reports that routing without claiming the connection is ready.

The installer finishes by running `python3 ~/.agents/codex/check_connections.py`. Run it independently whenever a connection is missing. It launches Codex's app server and uses the read-only `app/installed` check with a fresh runtime snapshot, requiring both `enabled` and `callable`. It does not start a model turn, read messages, install plugins, sign in, change permissions, or read credential files. Codex itself handles its normal authentication and local runtime cache. See the [official app runtime check](https://learn.chatgpt.com/docs/app-server#apps-connectors).

The check reports `TOOLS AVAILABLE` only after observing callable tools, `ACTION REQUIRED` for missing/disabled connections, and `UNVERIFIED` for timeouts, unsupported commands, or other check failures. Exit codes are 0, 1, and 2 respectively; an incomplete check makes installation exit nonzero even though local files were installed. The default timeout is 30 seconds; override with `--timeout SECONDS`. Existing direct server configurations are preserved and checked for a connected runtime with tools; saved credentials alone do not pass.

For Slack account setup, open the app's **Plugins** tab, find **Slack**, install it if missing, and complete its account connection. From terminal Codex, run `codex`, then `/plugins`. Start a new chat after installation, then rerun the check. The checker cannot prove access to a specific private message or make tools appear in an already-open chat. Other direct server connections are outside this check's scope. See the [official plugin setup steps](https://learn.chatgpt.com/docs/plugins#install-and-use-a-plugin).

## Codex setup

`codex/apply.py` adds `CLAUDE.md` as a fallback project instruction filename; `AGENTS.override.md` and `AGENTS.md` still take precedence. The default project instruction budget here is 128 KiB because the backend's `CLAUDE.md` exceeds Codex's 32 KiB default. Existing custom filenames and an explicitly configured budget are preserved. It also adds `~/dev/.claude/worktrees`, where every skill creates its worktrees, to `sandbox_workspace_write.writable_roots`, so Codex edits and runs tests there without asking each time, and turns on `sandbox_workspace_write.network_access`, so `gh`, `git push`/`fetch`, eval runs and `npm` stop asking (Aakif's call, 2026-11-06: those were about 450 of 1,138 asks in one session); `.git` stays read-only, so commits still ask. A running Codex session keeps the sandbox it started with: start a new one to pick these up. See the [official instruction discovery documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

Skills are discovered natively in `~/.agents/skills`; the obsolete `features.skills` entry is removed when applying the setup. Shared skills' `$ARGUMENTS` placeholders mean the arguments supplied by the user, including when invoked as `$skill-name` in Codex. See [local skill discovery](https://learn.chatgpt.com/docs/build-skills).

Current Codex supports the shared `UserPromptSubmit` hooks. Both shell wrappers read their sections from `AGENTS.md` through `hooks/reminder.py`, so editing the instructions updates the per-turn reminder too. The voice is always on; there is no off switch. Hook definitions are in `codex/hooks.json`; use `/hooks` to review their trust status if Codex reports a new or changed definition. The installer does not grant hook trust or bypass approval settings. See [Codex hooks](https://learn.chatgpt.com/docs/hooks).

Validated with Codex CLI 0.153.4: instruction/skill/rule links, hook JSON, repeated config application, and project instruction discovery. Restart Codex after configuration changes to rebuild the initial instruction chain. Run the local checks with `python3 -m unittest discover -s tests`.

Not portable: claude.ai connectors (Slack, Linear, Notion OAuth through claude.ai), Claude plugins (Datadog), and Claude's auto-memory (permission allowlists are versioned but merged per machine). Those stay per tool.
