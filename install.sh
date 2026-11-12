#!/bin/bash
# Wire this repo into Claude Code, Codex, and Gemini CLI. Idempotent.
set -e
target="${1:-all}"
case "$target" in all|codex) ;; *) echo "usage: $0 [all|codex]" >&2; exit 2 ;; esac
A="$HOME/.agents"
link() { [ -L "$2" ] && [ "$(readlink "$2")" = "$1" ] && return; { [ -e "$2" ] || [ -L "$2" ]; } && { echo "exists, not touching: $2"; return; }; mkdir -p "$(dirname "$2")"; ln -s "$1" "$2"; echo "linked $2 -> $1"; }

# Claude Code
if [ "$target" = all ]; then
link "$A/skills"          "$HOME/.claude/skills"
link "$A/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
python3 "$A/claude/apply.py"
command -v claude >/dev/null && python3 "$A/mcp/render.py" claude --apply
fi

# Commands (the background tools' live views)
for cmd in watch-slack-claude watch-pr-reviews watch-on-call agents-engine; do link "$A/bin/$cmd" "$HOME/.local/bin/$cmd"; done

# Codex (reads ~/.agents/skills natively)
codex_dir="${CODEX_HOME:-$HOME/.codex}"
link "$A/AGENTS.md" "$codex_dir/AGENTS.md"
link "$A/codex/rules" "$codex_dir/rules"
link "$A/codex/hooks.json" "$codex_dir/hooks.json"
python3 "$A/codex/apply.py"
python3 "$A/mcp/render.py" codex --apply

# Gemini CLI
if [ "$target" = all ] && [ -d "$HOME/.gemini" ]; then
  link "$A/skills" "$HOME/.gemini/skills"
  python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.gemini/settings.json")
s = json.load(open(p)) if os.path.exists(p) else {}
s.setdefault("context", {})["fileName"] = ["AGENTS.md", "GEMINI.md"]
json.dump(s, open(p, "w"), indent=2)
PY
  link "$A/AGENTS.md" "$HOME/.gemini/AGENTS.md"
  python3 "$A/mcp/render.py" gemini
fi

# Finish all local writes before reporting any remaining account setup.
echo "Local installation finished. Checking declared Codex plugin connections..."
python3 "$A/codex/check_connections.py"
