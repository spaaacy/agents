#!/usr/bin/env python3
"""Merge claude/settings.hooks.json and claude/permissions.json into ~/.claude/settings.json.
Union semantics: never removes anything already there; hooks deduped by command, rules by string."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
path = os.path.expanduser("~/.claude/settings.json")
s = json.load(open(path)) if os.path.exists(path) else {}

hooks = json.load(open(os.path.join(HERE, "settings.hooks.json")))["hooks"]
for event, entries in hooks.items():
    cur = s.setdefault("hooks", {}).setdefault(event, [])
    have = {h["command"] for e in cur for h in e.get("hooks", [])}
    for e in entries:
        if not all(h["command"] in have for h in e["hooks"]):
            cur.append(e)

perms = json.load(open(os.path.join(HERE, "permissions.json")))["permissions"]
p = s.setdefault("permissions", {})
for key in ("allow", "additionalDirectories"):
    cur = p.setdefault(key, [])
    cur.extend(x for x in perms.get(key, []) if x not in cur)

json.dump(s, open(path, "w"), indent=2); open(path, "a").write("\n")
print(f"merged into {path}: {len(p['allow'])} allow rules, hooks: {', '.join(s['hooks'])}")
