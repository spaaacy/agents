"""The hub's local dashboard: one page and a small JSON API, served from the hub process on 127.0.0.1.

GET  /                         the page (dashboard.html)
GET  /api/token                the token POSTs need (same-origin pages only)
GET  /api/state                hub.snapshot() plus the last 100 jobs
GET  /api/jobs?plugin=&status=&limit=
GET  /api/jobs/<id>            one job and the last 400 lines of its agent's work
GET  /api/jobs/<id>/events?offset=N   lines added since byte N, and the new offset
POST /api/command {"words": [...]}    a control command (controls.py), as in Slack

Guard: the server listens on loopback only, every request must name this
server in its Host header (a page on another site cannot reach it through a
DNS trick), and every POST needs the token from /api/token in X-Hub-Token
plus either no Origin or this server's own origin. Another site's page can
send a request but cannot read the token, so it cannot change anything.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from . import events

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger("hub.dashboard")
PAGE = Path(__file__).resolve().parent / "dashboard.html"
JOB_PATH = re.compile(r"^/api/jobs/([A-Za-z0-9-]{1,40})(/events)?$")
MAX_BODY = 16_384
EVENT_TAIL = 400


def token_file(hub: Hub) -> Path:
    return hub.state_dir / "dashboard.token"


def allowed_hosts(host: str, port: int) -> set[str]:
    names = {host}
    if host in ("127.0.0.1", "localhost", "::1"):
        names |= {"127.0.0.1", "localhost"}
    return {f"{name}:{port}" for name in names}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, hub: Hub, host: str, port: int, token: str) -> None:
        self.hub = hub
        self.token = token
        super().__init__((host, port), Handler)
        self.port = self.server_address[1]
        self.hosts = allowed_hosts(host, self.port)
        self.origins = {f"http://{h}" for h in self.hosts}


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "agents-hub"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler's name
        log.debug("%s %s", self.address_string(), format % args)

    # ------------------------------------------------------------------ plumbing

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data, default=str).encode(), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.server.hosts

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in self.server.origins

    # ------------------------------------------------------------------ GET

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "unknown host")
        url = urlparse(self.path)
        hub = self.server.hub
        try:
            if url.path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/token":
                if not self._origin_ok():
                    return self._error(HTTPStatus.FORBIDDEN, "cross-origin")
                return self._json({"token": self.server.token})
            if url.path == "/api/state":
                state = hub.snapshot()
                state["jobs"] = [job.to_dict() for job in hub.store.jobs(limit=100)]
                return self._json(state)
            if url.path == "/api/jobs":
                query = parse_qs(url.query)
                limit = min(500, max(1, int((query.get("limit") or ["50"])[0])))
                jobs = hub.store.jobs(plugin=(query.get("plugin") or [None])[0] or None,
                                      status=(query.get("status") or [None])[0] or None, limit=limit)
                return self._json({"jobs": [job.to_dict() for job in jobs]})
            match = JOB_PATH.match(url.path)
            if match:
                job = hub.store.get(match.group(1))
                if job is None:
                    return self._error(HTTPStatus.NOT_FOUND, "no such job")
                path = hub.state_dir / "runs" / job.id / "events.jsonl"
                if match.group(2):
                    offset = max(0, int((parse_qs(url.query).get("offset") or ["0"])[0]))
                    lines, offset = events.read_events(path, offset)
                    return self._json({"lines": lines, "offset": offset, "status": job.status})
                lines, offset = events.tail_events(path, EVENT_TAIL)
                return self._json({"job": job.to_dict(), "events": lines, "offset": offset})
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "bad query")
        except Exception as exc:  # noqa: BLE001 - one bad request must not take the page down
            log.exception("dashboard GET %s failed", url.path)
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ POST

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "unknown host")
        if not self._origin_ok():
            return self._error(HTTPStatus.FORBIDDEN, "cross-origin")
        if not secrets.compare_digest(self.headers.get("X-Hub-Token", ""), self.server.token):
            return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
        if urlparse(self.path).path != "/api/command":
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            return self._error(HTTPStatus.BAD_REQUEST, "body missing or too large")
        try:
            body = json.loads(self.rfile.read(length))
            words = body["words"]
            if not isinstance(words, list) or not 0 < len(words) <= 20 or not all(isinstance(w, str) for w in words):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self._error(HTTPStatus.BAD_REQUEST, 'expected {"words": ["status"]}')
        try:
            reply = self.server.hub.command(words)
        except Exception as exc:  # noqa: BLE001
            log.exception("dashboard command %s failed", words)
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")
        return self._json({"reply": reply})


def serve_dashboard(hub: Hub) -> DashboardServer | None:
    """Start the dashboard on a daemon thread; None (logged) when its port is taken."""
    dash = hub.cfg.get("dashboard", {})
    host, port = dash.get("host", "127.0.0.1"), int(dash.get("port", 8765))
    token = secrets.token_urlsafe(24)
    path = token_file(hub)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(token + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)
    try:
        server = DashboardServer(hub, host, port, token)
    except OSError as exc:
        log.error("dashboard not started: %s:%s is unavailable (%s)", host, port, exc)
        return None
    threading.Thread(target=server.serve_forever, name="dashboard", daemon=True).start()
    log.info("dashboard on http://%s:%s", host, server.port)
    return server
