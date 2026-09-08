#!/bin/bash
# Wire this repo into Claude Code, Codex, and Gemini CLI. Idempotent.
set -e
A="$HOME/.agents"
link() { [ -L "$2" ] && [ "$(readlink "$2")" = "$1" ] && return; [ -e "$2" ] && { echo "exists, not touching: $2"; return; }; mkdir -p "$(dirname "$2")"; ln -s "$1" "$2"; echo "linked $2 -> $1"; }

# Claude Code
link "$A/skills"          "$HOME/.claude/skills"
link "$A/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
python3 "$A/claude/apply.py"
command -v claude >/dev/null && python3 "$A/mcp/render.py" claude --apply

# Codex (reads ~/.agents/skills natively)
link "$A/AGENTS.md" "$HOME/.codex/AGENTS.md"
link "$A/codex/rules" "$HOME/.codex/rules"
link "$A/codex/hooks.json" "$HOME/.codex/hooks.json"
grep -q "^skills = true" "$HOME/.codex/config.toml" 2>/dev/null || printf "\n[features]\nskills = true\n" >> "$HOME/.codex/config.toml"
python3 "$A/mcp/render.py" codex --apply

# Gemini CLI
if [ -d "$HOME/.gemini" ]; then
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


