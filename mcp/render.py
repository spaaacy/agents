#!/usr/bin/env python3
"""Render mcp/servers.json into each tool's format.

  render.py claude   -> `claude mcp add-json` commands (user scope)
  render.py codex    -> TOML fragment for ~/.codex/config.toml [mcp_servers.*]
  render.py codex --apply -> writes that fragment into ~/.codex/config.toml inside a
                             "# BEGIN/END agents-managed" block (servers already defined
                             elsewhere in the file are left alone, block is replaced on re-run)
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
    import io, tomllib
    path = os.path.expanduser("~/.codex/config.toml")
    apply = "--apply" in sys.argv
    existing = open(path).read() if os.path.exists(path) else ""
    BEGIN, END = "# BEGIN agents-managed (mcp/render.py codex --apply)", "# END agents-managed"
    if BEGIN in existing and END in existing:
        head, rest = existing.split(BEGIN, 1)
        unmanaged = head + rest.split(END, 1)[1]
    else:
        unmanaged = existing
    already = set(tomllib.loads(unmanaged).get("mcp_servers", {})) if unmanaged.strip() else set()
    skip = already | {"slack"}  # slack: Codex has its own plugin
    buf = io.StringIO()
    for name, cfg in servers.items():
        if name in skip:
            print(f"# {name}: already in config.toml outside the managed block (or plugin), skipped", file=sys.stderr)
            continue
        print(f"[mcp_servers.{name}]", file=buf)
        if cfg["type"] == "http":
            print(f'url = "{cfg["url"]}"', file=buf)
            if cfg.get("headers"):
                print(f"[mcp_servers.{name}.http_headers]", file=buf)
                for k, v in cfg["headers"].items():
                    print(f'{k} = "{v}"', file=buf)
        else:
            print(f'command = "{cfg["command"]}"', file=buf)
            print(f'args = {json.dumps(cfg.get("args", []))}', file=buf)
            if cfg.get("env"):
                print(f"[mcp_servers.{name}.env]", file=buf)
                for k, v in cfg["env"].items():
                    print(f'{k} = "{v}"', file=buf)
        print(file=buf)
    fragment = buf.getvalue()
    if not apply:
        print(fragment, end="")
    else:
        merged = unmanaged.rstrip("\n") + "\n\n" + BEGIN + "\n" + fragment + END + "\n"
        tomllib.loads(merged)  # refuse to write invalid TOML
        open(path, "w").write(merged)
        print(f"wrote managed block with {fragment.count('[mcp_servers.')-fragment.count('.env]')-fragment.count('.http_headers]')} servers into {path}")

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
