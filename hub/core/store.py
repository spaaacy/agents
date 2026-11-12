"""The hub's one record of work: a job queue every plugin shares, and a small key-value store per plugin.

A job is one unit of agent work (answer a Slack question, investigate a page,
review a PR head). Plugins enqueue jobs; the scheduler claims them by priority
and hands them back to their plugin. Every job keeps what the dashboard,
`agents status` and the Slack controls show: its state, the engine and session
that ran it, the link to what it produced, its cost.

SQLite in WAL mode, one short connection per call, so the scheduler threads,
the dashboard and a second `agents` process can all read and write it.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

STATUSES = ("queued", "running", "done", "failed", "cancelled")
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    plugin TEXT NOT NULL,
    title TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'queued',
    dedupe TEXT UNIQUE,
    payload TEXT NOT NULL DEFAULT '{}',
    created REAL NOT NULL,
    started REAL,
    finished REAL,
    engine TEXT,
    session TEXT,
    link TEXT,
    summary TEXT,
    error TEXT,
    tokens INTEGER,
    cost REAL,
    attempt INTEGER NOT NULL DEFAULT 1,
    retry_of TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, priority DESC, created);
CREATE INDEX IF NOT EXISTS jobs_plugin ON jobs(plugin, created DESC);
CREATE TABLE IF NOT EXISTS kv (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);
"""
UPDATABLE = {"title", "engine", "session", "link", "summary", "error", "tokens", "cost", "payload"}


@dataclass
class Job:
    id: str
    plugin: str
    title: str
    priority: int
    status: str
    payload: dict[str, Any]
    created: float
    dedupe: str | None = None
    started: float | None = None
    finished: float | None = None
    engine: str | None = None
    session: str | None = None
    link: str | None = None
    summary: str | None = None
    error: str | None = None
    tokens: int | None = None
    cost: float | None = None
    attempt: int = 1
    retry_of: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        data = dict(row)
        data["payload"] = json.loads(data.get("payload") or "{}")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class JobResult:
    """What a plugin's handle() returns. ``status`` is done or failed."""

    status: str = "done"
    summary: str | None = None
    link: str | None = None
    error: str | None = None
    engine: str | None = None
    session: str | None = None
    tokens: int | None = None
    cost: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # merged into the job's payload


def new_id() -> str:
    return time.strftime("%m%d") + "-" + secrets.token_hex(3)


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ jobs

    def enqueue(self, plugin: str, title: str, payload: dict[str, Any] | None = None, *, priority: int = 50,
                dedupe: str | None = None, retry_of: str | None = None, attempt: int = 1) -> Job | None:
        """Add a job; None when one with the same dedupe key already exists (a redelivered event)."""
        job_id = new_id()
        with self._db() as db:
            try:
                db.execute(
                    "INSERT INTO jobs(id,plugin,title,priority,status,dedupe,payload,created,attempt,retry_of) "
                    "VALUES (?,?,?,?, 'queued', ?,?,?,?,?)",
                    (job_id, plugin, title, priority, dedupe, json.dumps(payload or {}), time.time(), attempt, retry_of),
                )
            except sqlite3.IntegrityError:
                return None
        return self.get(job_id)

    def get(self, job_id: str) -> Job | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return Job.from_row(row) if row else None

    def find(self, prefix: str) -> Job | None:
        """A job by its id or a unique prefix of it (what people type in Slack)."""
        with self._db() as db:
            rows = db.execute("SELECT * FROM jobs WHERE id LIKE ? ORDER BY created DESC LIMIT 2", (prefix + "%",)).fetchall()
        return Job.from_row(rows[0]) if len(rows) == 1 else None

    def by_dedupe(self, dedupe: str) -> Job | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE dedupe=?", (dedupe,)).fetchone()
        return Job.from_row(row) if row else None

    def claim(self, blocked: set[str], running_limit: dict[str, int]) -> Job | None:
        """Take the highest-priority queued job whose plugin is not paused and has a free slot."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                running = {r["plugin"]: r["n"] for r in db.execute(
                    "SELECT plugin, COUNT(*) AS n FROM jobs WHERE status='running' GROUP BY plugin")}
                for row in db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY priority DESC, created"):
                    plugin = row["plugin"]
                    if plugin in blocked or running.get(plugin, 0) >= running_limit.get(plugin, 1):
                        continue
                    db.execute("UPDATE jobs SET status='running', started=? WHERE id=?", (time.time(), row["id"]))
                    db.execute("COMMIT")
                    return self.get(row["id"])
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return None

    def requeue(self, job_id: str) -> None:
        """Hand a claimed job back to the queue untouched (its plugin was paused as it was claimed)."""
        with self._db() as db:
            db.execute("UPDATE jobs SET status='queued', started=NULL WHERE id=? AND status='running'", (job_id,))

    def update(self, job_id: str, **fields: Any) -> None:
        unknown = set(fields) - UPDATABLE
        if unknown:
            raise ValueError(f"cannot update {unknown}")
        if "payload" in fields:
            fields["payload"] = json.dumps(fields["payload"])
        if not fields:
            return
        with self._db() as db:
            db.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?", (*fields.values(), job_id))

    def finish(self, job_id: str, result: JobResult) -> None:
        job = self.get(job_id)
        payload = {**(job.payload if job else {}), **result.extra}
        with self._db() as db:
            db.execute(
                "UPDATE jobs SET status=?, finished=?, summary=?, link=?, error=?, engine=COALESCE(?, engine), "
                "session=COALESCE(?, session), tokens=?, cost=?, payload=? WHERE id=?",
                (result.status if result.status in STATUSES else "done", time.time(), result.summary, result.link,
                 result.error, result.engine, result.session, result.tokens, result.cost, json.dumps(payload), job_id),
            )

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued job. A running one is cancelled through the scheduler."""
        with self._db() as db:
            changed = db.execute("UPDATE jobs SET status='cancelled', finished=? WHERE id=? AND status='queued'",
                                 (time.time(), job_id)).rowcount
        return bool(changed)

    def retry(self, job_id: str) -> Job | None:
        """A fresh queued copy of a finished job (same payload, no dedupe key)."""
        job = self.get(job_id)
        if not job or job.status in ("queued", "running"):
            return None
        return self.enqueue(job.plugin, job.title, job.payload, priority=job.priority, retry_of=job.id,
                            attempt=job.attempt + 1)

    def recover_interrupted(self) -> list[Job]:
        """Jobs left running by a stopped hub are failed, never silently counted as done; returns them."""
        with self._db() as db:
            rows = db.execute("SELECT * FROM jobs WHERE status='running'").fetchall()
            db.execute("UPDATE jobs SET status='failed', finished=?, error='The hub stopped while this ran; retry it' "
                       "WHERE status='running'", (time.time(),))
        return [Job.from_row(r) for r in rows]

    def jobs(self, *, plugin: str | None = None, status: str | None = None, limit: int = 50) -> list[Job]:
        query, args = "SELECT * FROM jobs WHERE 1=1", []
        if plugin:
            query += " AND plugin=?"
            args.append(plugin)
        if status:
            query += " AND status=?"
            args.append(status)
        query += " ORDER BY created DESC LIMIT ?"
        args.append(limit)
        with self._db() as db:
            return [Job.from_row(r) for r in db.execute(query, args)]

    def counts(self) -> dict[str, dict[str, int]]:
        """{plugin: {status: n}} over the last week, for the dashboard header."""
        with self._db() as db:
            rows = db.execute("SELECT plugin, status, COUNT(*) AS n FROM jobs WHERE created > ? GROUP BY plugin, status",
                              (time.time() - 7 * 86400,)).fetchall()
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["plugin"], {})[r["status"]] = r["n"]
        return out

    # ------------------------------------------------------------------ key-value

    def get_value(self, scope: str, key: str, default: Any = None) -> Any:
        with self._db() as db:
            row = db.execute("SELECT value FROM kv WHERE scope=? AND key=?", (scope, key)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_value(self, scope: str, key: str, value: Any) -> None:
        with self._db() as db:
            db.execute("INSERT INTO kv VALUES (?,?,?) ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value",
                       (scope, key, json.dumps(value)))

    def values(self, scope: str) -> dict[str, Any]:
        """Every key in a scope (a plugin's reminders, say)."""
        with self._db() as db:
            rows = db.execute("SELECT key, value FROM kv WHERE scope=? ORDER BY key", (scope,)).fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def delete_value(self, scope: str, key: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM kv WHERE scope=? AND key=?", (scope, key))
