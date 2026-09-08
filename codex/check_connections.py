#!/usr/bin/env python3
"""Check plugin tool availability through Codex, without starting a model turn.

Exit 0: tools available for every declared connection. Exit 1: action needed.
Exit 2: readiness could not be verified. Never installs, signs in, or changes
permissions. Codex uses its own normal authentication; this script reads no tokens.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tomllib

CONNECTIONS = Path(__file__).with_name("connections.json")


class CheckError(Exception):
    pass


async def runtime_snapshot(command: list[str], *, direct: bool = False) -> dict:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=8 * 1024 * 1024,
    )
    request_id = 0

    async def send(payload):
        process.stdin.write((json.dumps(payload) + "\n").encode())
        await process.stdin.drain()

    async def request(method, params):
        nonlocal request_id
        request_id += 1
        await send({"id": request_id, "method": method, "params": params})
        while line := await process.stdout.readline():
            response = json.loads(line)
            if response.get("id") == request_id and "method" not in response:
                if "error" in response:
                    # Do not print server error bodies: they may include private config.
                    code = response["error"].get("code", "unknown")
                    raise CheckError(f"Codex rejected {method} (code {code})")
                if "result" not in response:
                    raise CheckError(f"Codex returned no result for {method}")
                return response["result"]
            if "method" in response and "id" in response:
                # This diagnostic never grants permissions or answers sign-in prompts.
                await send({"id": response["id"], "error": {
                    "code": -32601, "message": "Not supported by the read-only connection check"
                }})
        raise CheckError("Codex exited before completing the connection check")

    try:
        await request("initialize", {"clientInfo": {
            "name": "agents_connection_check", "version": "1.0.0"
        }})
        await send({"method": "initialized", "params": {}})
        apps = await request("app/installed", {"forceRefresh": True})
        if not isinstance(apps.get("apps"), list):
            raise CheckError("Codex returned an unrecognized installed-app response")
        servers = []
        if direct:
            cursor = None
            seen = set()
            while True:
                result = await request("mcpServerStatus/list", {
                    "detail": "toolsAndAuthOnly", "cursor": cursor, "limit": 100
                })
                if not isinstance(result.get("data"), list):
                    raise CheckError("Codex returned an unrecognized server-status response")
                servers.extend(result["data"])
                cursor = result.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen:
                    raise CheckError("Codex repeated a server-status page")
                seen.add(cursor)
        return {"apps": apps["apps"], "servers": servers}
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()


def assess(connections: dict, config: dict, snapshot: dict) -> list[tuple[str, str, str]]:
    apps = {app["id"]: app for app in snapshot["apps"]}
    servers = {server["name"]: server for server in snapshot["servers"]}
    results = []
    for key, connection in connections.items():
        name = connection["name"]
        direct = config.get("mcp_servers", {}).get(key)
        if direct is not None:
            server = servers.get(key, {})
            if direct.get("enabled", True) is False or server.get("runtimeStatus") == "disabled":
                status, detail = "ACTION REQUIRED", f"The direct server is disabled; review mcp_servers.{key}.enabled."
            elif server.get("authStatus") == "notLoggedIn" or server.get("runtimeStatus") == "authenticationRequired":
                status, detail = "ACTION REQUIRED", f"Sign in with: codex mcp login {key}"
            elif server.get("tools") and server.get("runtimeStatus") == "connected":
                status, detail = "TOOLS AVAILABLE", "The direct server exposes tools to Codex."
            else:
                status, detail = "UNVERIFIED", "Direct server configured, but no connected tools were observed; inspect /mcp in Codex."
        else:
            app = apps.get(connection["app_id"])
            if app and app.get("enabled") is True and app.get("callable") is True:
                status, detail = "TOOLS AVAILABLE", "Codex reports enabled, callable plugin tools for new chats."
            elif app and app.get("enabled") is False:
                status, detail = "ACTION REQUIRED", f"Access is disabled by settings or policy; review {name} in Plugins."
            else:
                status = "ACTION REQUIRED"
                detail = (
                    f"No callable plugin tools were observed. Open Plugins, find {name}, install if missing, "
                    "and complete the account connection. In terminal Codex, use /plugins. "
                    "Then start a new chat and rerun this check."
                )
        results.append((status, name, detail))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=30, help="Maximum runtime-check seconds (default: 30)")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    print("Checking account tool availability; local plugin flags alone do not prove a connection.", flush=True)
    try:
        connections = json.loads(CONNECTIONS.read_text())
        codex_dir = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        path = codex_dir / "config.toml"
        config = tomllib.loads(path.read_text()) if path.exists() else {}
        direct = any(key in config.get("mcp_servers", {}) for key in connections)
        snapshot = asyncio.run(asyncio.wait_for(
            runtime_snapshot(["codex", "app-server"], direct=direct), timeout=args.timeout
        ))
        results = assess(connections, config, snapshot)
    except (OSError, ValueError, TypeError, KeyError, CheckError, asyncio.TimeoutError) as error:
        # Exception text may contain secrets from a config/transport parse failure.
        reason = str(error) if isinstance(error, CheckError) else type(error).__name__
        print(f"UNVERIFIED: {reason}. Check Codex sign-in/network access, then rerun python3 ~/.agents/codex/check_connections.py.")
        return 2
    for status, name, detail in results:
        print(f"{status}: {name}. {detail}")
    print("This checks declared plugin connections, not access to a particular message or tools in an already-open chat.")
    if any(status == "UNVERIFIED" for status, _, _ in results):
        return 2
    return int(any(status != "TOOLS AVAILABLE" for status, _, _ in results))


if __name__ == "__main__":
    sys.exit(main())
