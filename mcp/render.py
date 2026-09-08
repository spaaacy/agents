#!/usr/bin/env python3
"""Render mcp/servers.json into each tool's format.

  render.py claude   -> `claude mcp add-json` commands (user scope)
  render.py codex    -> TOML fragment for ~/.codex/config.toml [mcp_servers.*]
  render.py gemini   -> merges into ~/.gemini/settings.json "mcpServers" (or prints if absent)

Secrets come from mcp/secrets.env as ${VAR}; unset vars leave the server out with a warning.
"""
import json, os, re, shlex, sys

HERE = os.path.dirname(os.path.abspath(__file__))
env = dict(os.environ)
envfile = os.path.join(HERE, "secrets.env")
if os.path.exists(envfile):
    for line in open(envfile):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v

def sub(obj, missing):
    if isinstance(obj, dict):
        return {k: sub(v, missing) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sub(v, missing) for v in obj]
    if isinstance(obj, str):
        def rep(m):
            val = env.get(m.group(1), "")
            if not val:
                missing.add(m.group(1))
            return val
        return re.sub(r"\$\{(\w+)\}", rep, obj)
    return obj

servers = {}
for name, cfg in json.load(open(os.path.join(HERE, "servers.json"))).items():
    missing = set()
    rendered = sub(cfg, missing)
    if missing:
        print(f"# skipping {name}: unset {', '.join(sorted(missing))}", file=sys.stderr)
        continue
    servers[name] = rendered

target = sys.argv[1] if len(sys.argv) > 1 else "claude"

if target == "claude":
    for name, cfg in servers.items():
        print(f"claude mcp add-json --scope user {shlex.quote(name)} {shlex.quote(json.dumps(cfg))}")

elif target == "codex":
    for name, cfg in servers.items():
        print(f"[mcp_servers.{name}]")
        if cfg["type"] == "http":
            print(f'url = "{cfg["url"]}"')
            if cfg.get("headers"):
                print(f"[mcp_servers.{name}.http_headers]")
                for k, v in cfg["headers"].items():
                    print(f'{k} = "{v}"')
        else:
            print(f'command = "{cfg["command"]}"')
            print(f'args = {json.dumps(cfg.get("args", []))}')
            if cfg.get("env"):
                print(f"[mcp_servers.{name}.env]")
                for k, v in cfg["env"].items():
                    print(f'{k} = "{v}"')
        print()

elif target == "gemini":
    out = {}
    for name, cfg in servers.items():
        if cfg["type"] == "http":
            g = {"httpUrl": cfg["url"]}
            if cfg.get("headers"):
                g["headers"] = cfg["headers"]
        else:
            g = {"command": cfg["command"], "args": cfg.get("args", [])}
            if cfg.get("env"):
                g["env"] = cfg["env"]
        out[name] = g
    path = os.path.expanduser("~/.gemini/settings.json")
    if os.path.exists(path):
        settings = json.load(open(path))
        settings.setdefault("mcpServers", {}).update(out)
        json.dump(settings, open(path, "w"), indent=2)
        print(f"merged {len(out)} servers into {path}")
    else:
        print(json.dumps({"mcpServers": out}, indent=2))
else:
    sys.exit(f"unknown target {target}")
