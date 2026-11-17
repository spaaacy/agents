"""PR reviews from #devs: the hub plugin that replaced ~/.agents/slack-pr-review.

Source: a thread searches #devs through Codex's Slack sign-in once a minute and
queues one hub job per (message, PR) that mentions the owner. Handle: one
review of the PR's exact head in its own checkout, two agent turns (the review
skill, then post-review's draft as structured output) on the reviews setting
(engine, model and effort of its own: `!model reviews astra`), the draft checks, and a GitHub COMMENT review when posting is on.
The owner can also ask straight from the bot's DM: `!review <PR link or backend#3134> ...` queues the same
job for each PR, runs even while the #devs pickup is off, and answers in that message's thread: the draft
with Post and Skip, or one line (posted, already reviewed, failed).
Everything the checks and the publisher rely on is in domain.py.

The review queue keeps its own SQLite file (settings, requests, reviewed
heads) in this plugin's state folder; on first use it is copied from
~/.agents/slack-pr-review/state/state.sqlite3 so the checkpoint, the
enable/posting settings and the record of reviewed heads carry over.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ...core import agent, controls, engine
from ...core.plugin import Check, JobContext, Plugin
from ...core.store import Job, JobResult
from . import domain

HERE = Path(__file__).resolve().parent
CLAUDE_SETTINGS = HERE / "claude-settings.json"
OLD_DB = Path.home() / ".agents" / "slack-pr-review" / "state" / "state.sqlite3"
LOG = domain.LOG


class Cancelled(Exception):
    pass


def resume_command(checkout: Path | None, session: str | None) -> str | None:
    """How to reopen a review's session: inside its checkout, where the session ran."""
    if not checkout or not session:
        return None
    name, sid = agent.split_session(session)
    return f"cd {checkout} && codex resume {sid}" if name == "codex" else f"cd {checkout} && claude --resume {sid}"


def reply_target(row: sqlite3.Row) -> dict[str, str] | None:
    """The Slack thread a `!review` came from, or None (a #devs tag, or a `!review` from outside Slack)."""
    if row["source"] != "dm" or not row["reply_channel"]:
        return None
    return {"channel": row["reply_channel"], "thread_ts": row["reply_ts"]}


def brief(outcome: JobResult) -> str:
    """A review's outcome in one short line, never the review itself."""
    if outcome.status == "failed":
        error = (outcome.error or "no reason given").strip().splitlines()[0][:200]
        line = f"review failed: {error}"
    else:
        status = (outcome.extra or {}).get("review_status")
        line = {"posted": "posted", "superseded": "the PR changed during the review, so nothing got posted"}.get(
            status, outcome.summary or "done")
    return f"{line}, <{outcome.link}|see it on GitHub>" if outcome.link and outcome.status != "failed" else line


class CliContext:
    """What `agents pr_review draft` gives the review instead of a hub job: a run folder, no store."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.cancel = threading.Event()
        self.log = LOG

    def run_agent(self, task: agent.AgentTask) -> agent.AgentResult:
        task.run_dir = task.run_dir or self.run_dir
        return agent.run(task, self.cancel)

    def note(self, summary: str) -> None:
        print(summary, flush=True)


class PrReview(Plugin):
    name = "pr_review"
    title = "PR reviews from #devs"
    priority = 30
    max_concurrent = 1

    # ------------------------------------------------------------------ config and state

    @property
    def db_path(self) -> Path:
        return self.state_dir / "state.sqlite3"

    @property
    def reviews_dir(self) -> Path:
        """Where review checkouts go: outside ~/.agents, where an editor's Python indexer crawled each new
        checkout and stalled git for minutes (a backend checkout took 6 minutes there, 6 seconds outside)."""
        return Path(self.config.get("reviews_dir", "~/Library/Caches/agents-hub/pr-reviews")).expanduser()

    def domain_cfg(self) -> dict[str, Any]:
        """The old runner's config shape: this plugin's config plus the hub's owner."""
        owner = self.hub.cfg["owner"]
        return {**self.config, "user_id": owner["slack_user_id"], "github_login": owner["github_login"],
                "owner": self.config.get("org", "envoy-ai")}

    def db(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            self._migrate()
        return domain.database(self.db_path)

    def _migrate(self, force: bool = False) -> None:
        """Copy the old runner's queue (the backup API, safe while the old process still runs).

        Offline uses (`agents status`, `check`, a CLI command) copy it when there is
        no queue yet; the first real start of the hub copies it again (force), so
        what the old runner did after an early copy is never lost or redone.
        """
        if not OLD_DB.exists() or (self.db_path.exists() and not force):
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(OLD_DB, timeout=20)
        target = sqlite3.connect(self.db_path)
        try:
            source.backup(target)
            # The engine switch decides who reviews; the old per-tool choice is gone.
            target.execute("DELETE FROM settings WHERE key='reviewer'")
            target.commit()
        finally:
            target.close()
            source.close()
        LOG.info("copied the review queue from %s", OLD_DB)

    # ------------------------------------------------------------------ source

    def start(self) -> None:
        db = self.db()
        if not domain.setting(db, "hub_started_at"):
            db.close()
            self._migrate(force=True)
            db = self.db()
            with db:
                domain.put_setting(db, "hub_started_at", time.time())
        try:
            # An interrupted review must never be silently reported as completed.
            with db:
                db.execute("UPDATE jobs SET status='failed',error='The hub stopped during this review; retry it' "
                           "WHERE status='reviewing'")
                db.execute("UPDATE requests SET status='failed',error='The hub stopped during this review' "
                           "WHERE status='running'")
        finally:
            db.close()
        self.spawn(lambda: asyncio.run(self._collect()), "collect")

    async def _collect(self) -> None:
        cfg = self.domain_cfg()
        slack = None
        try:
            while not self.hub.stopping.is_set():
                db = self.db()
                try:
                    if domain.setting(db, "enabled_at"):
                        try:
                            if not slack:
                                slack = domain.Slack(cfg)
                                await slack.connect()
                            await domain.scan(cfg, db, slack)
                            with db:
                                domain.put_setting(db, "last_scan_ok", time.time())
                                domain.put_setting(db, "scan_error", "")
                        except Exception as error:  # noqa: BLE001 - the next scan retries; the checkpoint held
                            LOG.error("Slack scan failed: %s", error)
                            with db:
                                domain.put_setting(db, "scan_error", str(error))
                            if slack:
                                await slack.close()
                                slack = None
                        self.enqueue_queued(db)
                finally:
                    db.close()
                await asyncio.to_thread(self.hub.stopping.wait, cfg["poll_seconds"])
        finally:
            if slack:
                await slack.close()

    def enqueue_queued(self, db: sqlite3.Connection) -> list[Job]:
        """A hub job for every queued request; the dedupe key makes a second call a no-op."""
        cfg = self.domain_cfg()
        queued = []
        for row in db.execute("SELECT * FROM requests WHERE status='queued' ORDER BY CAST(message_ts AS REAL), id"):
            job = self.enqueue_request(row, cfg)
            if job:
                queued.append(job)
        return queued

    def enqueue_request(self, row: sqlite3.Row, cfg: dict[str, Any]) -> Job | None:
        repo, number = row["repo"], row["number"]
        return self.enqueue(f"{repo}#{number}", {
            "request_id": row["id"], "message_ts": row["message_ts"], "repo": repo, "number": number,
            "url": f"https://github.com/{repo}/pull/{number}", "channel": cfg["channel_id"],
            "direct": row["source"] == "dm", "reply": reply_target(row),
        }, dedupe=f"{row['message_ts']}:{repo}:{number}")

    # ------------------------------------------------------------------ asked for in the DM: !review

    def aliases(self) -> dict[str, Callable[[list[str]], str]]:
        return {"review": self.cmd_review}

    def cmd_review(self, args: list[str]) -> str:
        """`!review <PR link or backend#3134> ...`: review each PR now, the same job a #devs tag queues."""
        if not args:
            return "which PR? `!review <PR link>` or `!review backend#3134`"
        cfg = self.domain_cfg()
        found: list[tuple[str, int]] = []
        unread = []
        for word in args:
            ref = domain.pr_reference(word, cfg["owner"], cfg.get("local_repos", {}))
            if ref is None:
                unread.append(word)
            elif ref not in found:
                found.append(ref)
        if unread:
            return (f"couldn't read {', '.join(unread)} as one of {cfg['owner']}'s PRs: "
                    "give the PR link or `repo#number` (backend#3134)")
        thread = controls.command_thread() or {}
        db = self.db()
        try:
            names = []
            for repo, number in found:
                with db:
                    cursor = db.execute(
                        "INSERT INTO requests(message_ts,repo,number,source,reply_channel,reply_ts) "
                        "VALUES (?,?,?,'dm',?,?)",
                        (f"{time.time():.6f}", repo, number, thread.get("channel"), thread.get("thread_ts")))
                row = db.execute("SELECT * FROM requests WHERE id=?", (cursor.lastrowid,)).fetchone()
                self.enqueue_request(row, cfg)
                names.append(f"{repo.split('/', 1)[-1]}#{number}")
            posting = (domain.setting(db, "enabled_at") and domain.setting(db, "mode") == "post"
                       and domain.setting(db, "posting_authorized_at"))
        finally:
            db.close()
        then = "posts to GitHub when it's done" if posting else "the draft comes back here"
        return f"on it: {', '.join(names)} on {engine.describe(engine.current(scope='reviews'))}, {then}"

    def tell_owner(self, job: Job, outcome: JobResult) -> None:
        """One line under the `!review` when no draft came back to decide on: posted, already reviewed, failed."""
        if outcome.status == "cancelled" or (outcome.extra or {}).get("review_status") == "drafted":
            return
        slack = self.hub.slack
        if slack is None:
            return
        reply = job.payload.get("reply") or {}
        line = brief(outcome)
        if not reply.get("thread_ts"):
            line = f"{job.title}: {line}"  # a top-level DM has no `!review` above it to say which PR
        try:
            if reply.get("channel"):
                slack.post(reply["channel"], line, reply.get("thread_ts"))
            else:
                slack.dm_owner(line)
        except Exception:  # noqa: BLE001 - the dashboard has the outcome either way
            self.log.warning("could not send the review outcome", exc_info=True)

    # ------------------------------------------------------------------ one review

    def handle(self, job: Job, ctx: JobContext) -> JobResult:
        return asyncio.run(self._handle(job, ctx))

    async def _handle(self, job: Job, ctx: Any) -> JobResult:
        cfg = self.domain_cfg()
        db = self.db()
        try:
            request = db.execute("SELECT * FROM requests WHERE id=?", (job.payload.get("request_id"),)).fetchone()
            if request is None:
                return JobResult("failed", error="the review request is missing from the queue")
            direct = request["source"] == "dm"
            if not direct and not domain.setting(db, "enabled_at"):
                with db:
                    db.execute("UPDATE requests SET status='skipped_disabled' WHERE id=? AND status='queued'",
                               (request["id"],))
                return JobResult("cancelled", summary="PR reviews are off (agents pr_review enable)",
                                 link=job.payload.get("url"))
            retry = job.attempt > 1
            if request["status"] not in ("queued", "running") and not (retry and request["status"] == "failed"):
                return JobResult("done", summary=f"request already {request['status']}", link=job.payload.get("url"))
            outcome = await self.review(request, ctx, cfg, db, retry=retry)
            if direct:
                self.tell_owner(job, outcome)
            return outcome
        finally:
            db.close()

    async def review(self, request: sqlite3.Row, ctx: Any, cfg: dict[str, Any], db: sqlite3.Connection, *,
                     retry: bool = False, force_draft: bool = False) -> JobResult:
        key = None
        checkout: Path | None = None
        session: str | None = None
        try:
            pr = await domain.github(cfg, f"repos/{request['repo']}/pulls/{request['number']}")
            if pr["state"] != "open":
                with db:
                    db.execute("UPDATE requests SET status='closed' WHERE id=?", (request["id"],))
                return JobResult("done", summary="PR is closed; not reviewed", link=pr["html_url"])
            head, base = pr["head"]["sha"], pr["base"]["sha"]
            key = domain.job_key(request["repo"], request["number"], head, base)
            directory = self.reviews_dir / key
            existing = db.execute(
                "SELECT * FROM jobs WHERE key=? OR (repo=? AND number=? AND head=? "
                "AND status IN ('drafted','posted','posting','uncertain')) ORDER BY updated DESC LIMIT 1",
                (key, request["repo"], request["number"], head)).fetchone()
            if existing and retry and existing["key"] == key and existing["status"] == "failed":
                # Keep the failed attempt's artifacts, then review the same commit cleanly.
                old = Path(existing["directory"])
                if old.exists():
                    old.rename(old.with_name(old.name + f".failed-{int(time.time())}"))
                with db:
                    db.execute("DELETE FROM jobs WHERE key=?", (key,))
            elif existing:
                with db:
                    db.execute("UPDATE requests SET status='duplicate',job_key=? WHERE id=?", (existing["key"], request["id"]))
                link = existing["review_url"] or pr["html_url"]
                if existing["status"] in ("uncertain", "posting"):
                    return JobResult("failed", link=link, error="An earlier post of this review was never confirmed; "
                                     "inspect GitHub before retrying by hand")
                if existing["status"] == "failed":
                    return JobResult("done", link=link, summary=f"the review of {head[:12]} failed earlier; retry that job")
                return JobResult("done", link=link, summary=f"already reviewed at {head[:12]} ({existing['status']})")

            directory.mkdir(parents=True, exist_ok=True)
            choice = engine.current(scope="reviews")  # reviews have their own engine/model/effort setting
            name = choice["engine"]
            with db:
                db.execute("INSERT INTO jobs(key,repo,number,head,base,status,directory,updated,reviewer) "
                           "VALUES(?,?,?,?,?,'reviewing',?,?,?)",
                           (key, request["repo"], request["number"], head, base, str(directory), time.time(), name))
                db.execute("UPDATE requests SET status='running',job_key=? WHERE id=?", (key, request["id"]))
            LOG.info("Reviewing %s at %s with %s", pr["html_url"], head[:12], name)
            ctx.note(f"reviewing at {head[:12]} with {engine.describe(choice)}")
            checkout = await domain.prepare(cfg, pr, directory)

            common: dict[str, Any] = {
                "cwd": checkout, "mode": "review", "timeout": int(cfg["review_timeout_seconds"]), "engine": name,
                "choice": choice,
                "claude_allowed_tools": list(cfg.get("claude_allowed_tools", [])),
                "claude_settings": CLAUDE_SETTINGS, "add_dirs": [str(domain.SKILLS)],
            }
            first = await asyncio.to_thread(ctx.run_agent, agent.AgentTask(prompt=domain.review_prompt(name, pr), **common))
            if first.error == "cancelled" or ctx.cancel.is_set():
                raise Cancelled
            if first.error or not first.text.strip() or not first.session:
                raise RuntimeError(f"{name} produced no completed review: {first.error or 'no session'}")
            session = first.session
            (directory / "review.md").write_text(first.text.strip() + "\n")
            domain.update_job(db, key, "reviewing", session=first.session)

            ctx.note("drafting the GitHub review")
            second = await asyncio.to_thread(ctx.run_agent, agent.AgentTask(
                prompt=domain.draft_prompt(name, pr), session=first.session, schema=domain.SCHEMA, **common))
            if second.error == "cancelled" or ctx.cancel.is_set():
                raise Cancelled
            if second.error or not isinstance(second.structured, dict):
                raise RuntimeError(f"{name} returned no draft: {second.error or 'no structured answer'}")
            session = second.session or first.session
            draft = second.structured
            (directory / "draft.json").write_text(json.dumps(draft, indent=2) + "\n")
            await domain.finish_review(pr, checkout, directory, key)
            domain.update_job(db, key, "drafted")
            if not force_draft:
                ctx.note("posting, if posting is on")
                await domain.publish(cfg, db, db.execute("SELECT * FROM jobs WHERE key=?", (key,)).fetchone())
                if db.execute("SELECT status FROM jobs WHERE key=?", (key,)).fetchone()["status"] == "drafted":
                    self.offer_draft(pr, key, directory, reply_target(request))  # posting isn't on: ask with buttons
            with db:
                db.execute("UPDATE requests SET status='done' WHERE id=?", (request["id"],))
            LOG.info("Review ready: %s", directory / "draft.md")

            row = db.execute("SELECT status, review_url FROM jobs WHERE key=?", (key,)).fetchone()
            body = str(draft.get("body", "")).strip()
            first_line = body.splitlines()[0] if body else "(empty draft)"
            if row["status"] == "posted":
                summary, link = f"posted: {first_line}", row["review_url"]
            elif row["status"] == "superseded":
                summary, link = f"not posted, the PR changed or closed during the review: {first_line}", pr["html_url"]
            else:
                summary, link = f"drafted: {first_line}", pr["html_url"]
            tokens = [r.tokens for r in (first, second) if r.tokens]
            costs = [r.cost_usd for r in (first, second) if r.cost_usd]
            return JobResult("done", summary=summary, link=link, engine=name, session=session,
                             tokens=sum(tokens) if tokens else None, cost=sum(costs) if costs else None,
                             extra={"review_key": key, "review_status": row["status"],
                                    "draft": str(directory / "draft.md"),
                                    "resume_command": resume_command(checkout, session)})
        except Cancelled:
            if key:
                domain.update_job(db, key, "failed", error="cancelled")
            with db:
                db.execute("UPDATE requests SET status='failed',error='cancelled' WHERE id=?", (request["id"],))
            return JobResult("cancelled", error="cancelled", session=session,
                             extra={"resume_command": resume_command(checkout, session)} if session else {})
        except Exception as error:  # noqa: BLE001 - recorded on the job and in the queue
            if key:
                row = db.execute("SELECT status FROM jobs WHERE key=?", (key,)).fetchone()
                if row and row[0] not in ("posting", "uncertain", "posted"):
                    domain.update_job(db, key, "failed", error=str(error))
            with db:
                db.execute("UPDATE requests SET status='failed',error=? WHERE id=?", (str(error), request["id"]))
            LOG.exception("Review failed for %s#%s", request["repo"], request["number"])
            extra: dict[str, Any] = {"review_key": key} if key else {}
            if session:
                extra["resume_command"] = resume_command(checkout, session)
            return JobResult("failed", error=str(error)[:500], session=session, extra=extra)

    # ------------------------------------------------------------------ approve-to-act: post or skip a draft

    def offer_draft(self, pr: dict[str, Any], key: str, directory: Path, reply: dict[str, Any] | None = None) -> str | None:
        """DM the owner a drafted review with "Post this review" and "Skip"; the draft itself goes in the thread.

        Only when posting isn't on (draft mode, or post mode without the standing authorization): the tap
        authorizes that one review. A `!review` gets both under its own message (``reply``). Returns the
        buttons message's ts, or None when Slack can't be reached.
        """
        slack = self.hub.slack
        if slack is None or slack.web is None or not slack.bot_dm:
            return None
        name = f"{pr['base']['repo']['full_name']}#{pr['number']}"
        channel, thread = (reply or {}).get("channel") or slack.bot_dm, (reply or {}).get("thread_ts")
        text = ("draft's ready, posting is off. it's below" if thread else
                f"Review draft for <{pr['html_url']}|{name}> at `{pr['head']['sha'][:12]}`: posting isn't on, "
                "so it's waiting on you. The draft is in the thread.")
        buttons = [
            self.offer("post", "Post this review", {"review_key": key, "pr": name, "url": pr["html_url"]},
                       style="primary", confirm=f"Post this review on {name} as one GitHub comment review?"),
            self.offer("skip", "Skip", {"review_key": key, "pr": name, "url": pr["html_url"]}),
        ]
        try:
            ts = slack.post_markdown(channel, text, thread, blocks=self.hub.actions.blocks(text, buttons))[0]
            draft = directory / "draft.md"
            if draft.exists():
                slack.post_markdown(channel, draft.read_text(), thread or ts)
            return ts
        except Exception:  # noqa: BLE001 - the draft stays on disk and in the dashboard either way
            self.log.warning("could not DM the review draft", exc_info=True)
            return None

    def act(self, name: str, args: dict[str, Any], job: Job, ctx: JobContext) -> JobResult:
        if name in ("post", "skip"):
            return asyncio.run(self._act_on_draft(name, args, job))
        return super().act(name, args, job, ctx)

    async def _act_on_draft(self, name: str, args: dict[str, Any], job: Job) -> JobResult:
        cfg = self.domain_cfg()
        db = self.db()
        pr = args.get("pr") or "the PR"
        try:
            row = db.execute("SELECT * FROM jobs WHERE key=?", (args.get("review_key"),)).fetchone()
            if row is None:
                outcome = JobResult("failed", error=f"no saved review for {pr} any more")
            elif row["status"] != "drafted":
                done = {"posted": "already posted", "skipped": "already skipped", "superseded": "the PR changed since"}
                outcome = JobResult("done", summary=f"{pr}: {done.get(row['status'], row['status'])}, nothing to do",
                                    link=row["review_url"] or args.get("url"))
            elif name == "skip":
                domain.update_job(db, row["key"], "skipped")
                outcome = JobResult("done", summary=f"{pr}: draft skipped, not posted", link=args.get("url"))
            else:
                try:
                    await domain.publish(cfg, db, row, approved=True)
                except Exception as error:  # noqa: BLE001 - publish leaves the row uncertain when unsure
                    LOG.exception("posting the approved review failed")
                    outcome = JobResult("failed", error=f"{type(error).__name__}: {error}"[:500], link=args.get("url"))
                else:
                    after = db.execute("SELECT * FROM jobs WHERE key=?", (row["key"],)).fetchone()
                    if after["status"] == "posted":
                        outcome = JobResult("done", summary=f"{pr}: review posted", link=after["review_url"])
                    elif after["status"] == "superseded":
                        outcome = JobResult("done", summary=f"{pr}: not posted, the PR changed or closed since the review",
                                            link=args.get("url"))
                    else:
                        outcome = JobResult("failed", error=after["error"] or f"not posted ({after['status']})",
                                            link=args.get("url"))
        finally:
            db.close()
        reply = job.payload.get("reply") or {}
        slack = self.hub.slack
        if reply.get("channel") and slack is not None and slack.web is not None:
            line = outcome.summary or f"Couldn't post it: {outcome.error}"
            if outcome.link and outcome.link not in line:
                line += f"\n{outcome.link}"
            try:
                slack.post(reply["channel"], line, reply.get("thread_ts"))
            except Exception:  # noqa: BLE001
                self.log.warning("could not reply in the thread", exc_info=True)
        return outcome

    # ------------------------------------------------------------------ dashboard, checks, commands

    def status(self) -> dict[str, Any]:
        db = self.db()
        try:
            counts = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) AS n FROM requests GROUP BY status")}
            last_ok = domain.setting(db, "last_scan_ok")
            return {
                "enabled": bool(domain.setting(db, "enabled_at")),
                "mode": domain.setting(db, "mode"),
                "posting_authorized": bool(domain.setting(db, "posting_authorized_at")),
                "last_scan_ok": float(last_ok) if last_ok else None,
                "scan_error": domain.setting(db, "scan_error") or None,
                "requests": counts,
            }
        finally:
            db.close()

    def checks(self) -> list[Check]:
        cfg = self.domain_cfg()
        out = []
        try:
            login = subprocess.run([cfg["gh"], "api", "user", "--jq", ".login"], capture_output=True, text=True,
                                   timeout=30).stdout.strip()
            out.append(Check("github", login == cfg["github_login"], f"signed in as {login or 'nobody'}"))
        except (OSError, subprocess.TimeoutExpired) as exc:
            out.append(Check("github", False, str(exc)))
        out.append(Check("codex CLI", Path(cfg["codex"]).exists(), cfg["codex"]))

        async def probe() -> None:
            slack = domain.Slack(cfg)
            try:
                await asyncio.wait_for(slack.connect(), 60)
            finally:
                await slack.close()

        try:
            asyncio.run(probe())
            out.append(Check("slack search through codex", True))
        except Exception as exc:  # noqa: BLE001
            out.append(Check("slack search through codex", False, str(exc)[:200]))
        state = self.status()
        out.append(Check("reviews", True, f"{'on' if state['enabled'] else 'off'}, mode {state['mode'] or '-'}, "
                         f"posting {'authorized' if state['posting_authorized'] else 'not authorized'}"))
        return out

    def commands(self) -> dict[str, Callable[[list[str]], str]]:
        def mode(_: list[str]) -> str:
            state = self.status()
            return (f"PR reviews {'on' if state['enabled'] else 'off'}, mode {state['mode'] or '-'}, posting "
                    f"{'authorized' if state['posting_authorized'] else 'not authorized'}")

        def enable(args: list[str]) -> str:
            if args[:1] != ["draft"]:
                return ("only `enable draft` works from here; posting authorization is terminal-only: "
                        "`agents pr_review enable --mode post --authorize-comment-posting`")
            db = self.db()
            try:
                domain.enable(db, "draft", False)
            finally:
                db.close()
            return "PR reviews on in draft mode, for new mentions only"

        def disable(_: list[str]) -> str:
            db = self.db()
            try:
                domain.disable(db)
            finally:
                db.close()
            return "PR reviews off; a review already running finishes but cannot post"

        return {"mode": mode, "enable": enable, "disable": disable}

    def cli(self) -> dict[str, Callable[[list[str]], int]]:
        return {"enable": self._cli_enable, "disable": self._cli_disable, "preview": self._cli_preview,
                "draft": self._cli_draft, "status": self._cli_status}

    def _cli_enable(self, args: list[str]) -> int:
        parser = argparse.ArgumentParser(prog="agents pr_review enable")
        parser.add_argument("--mode", choices=["draft", "post"], required=True)
        parser.add_argument("--authorize-comment-posting", action="store_true")
        opts = parser.parse_args(args)
        db = self.db()
        try:
            domain.enable(db, opts.mode, opts.authorize_comment_posting)
        except ValueError as exc:
            parser.error(str(exc))
        finally:
            db.close()
        print(f"Enabled for new mentions only; mode={opts.mode}")
        return 0

    def _cli_disable(self, args: list[str]) -> int:
        db = self.db()
        try:
            domain.disable(db)
        finally:
            db.close()
        print("Disabled; an in-flight review may finish, but cannot post")
        return 0

    def _cli_status(self, args: list[str]) -> int:
        db = self.db()
        try:
            print(json.dumps({
                "settings": dict(db.execute("SELECT key,value FROM settings").fetchall()),
                "jobs": [dict(r) for r in db.execute(
                    "SELECT key,repo,number,status,reviewer,session,review_url,error,directory FROM jobs "
                    "ORDER BY updated DESC LIMIT 20")],
                "requests": [dict(r) for r in db.execute("SELECT status,COUNT(*) AS count FROM requests GROUP BY status")],
            }, indent=2))
        finally:
            db.close()
        return 0

    def _cli_preview(self, args: list[str]) -> int:
        parser = argparse.ArgumentParser(prog="agents pr_review preview")
        parser.add_argument("--hours", type=int, default=24)
        opts = parser.parse_args(args)
        cfg = self.domain_cfg()

        async def run() -> list:
            slack = domain.Slack(cfg)
            db = domain.database(Path(":memory:"))  # a preview reads Slack only; it never touches the queue
            try:
                await slack.connect()
                return await domain.scan(cfg, db, slack, preview=True, after=str(time.time() - opts.hours * 3600))
            finally:
                db.close()
                await slack.close()

        results = asyncio.run(run())
        print(json.dumps([{"message_ts": ts, "pr": f"https://github.com/{repo}/pull/{n}"} for ts, repo, n in results],
                         indent=2))
        return 0

    def _cli_draft(self, args: list[str]) -> int:
        """Review one PR now and print the draft; never posts, whatever the mode."""
        parser = argparse.ArgumentParser(prog="agents pr_review draft")
        parser.add_argument("url")
        opts = parser.parse_args(args)
        cfg = self.domain_cfg()
        match = domain.PR.fullmatch(opts.url)
        if not match or match[1].lower() != cfg["owner"].lower():
            parser.error("Use an exact pull-request URL in the configured GitHub organization")
        db = self.db()
        try:
            with db:
                # Inserted as running, so the collector never queues it for the hub as well.
                cursor = db.execute("INSERT INTO requests(message_ts,repo,number,status) VALUES (?,?,?,'running')",
                                    (str(time.time()), f"{cfg['owner']}/{match[2]}", int(match[3])))
            request = db.execute("SELECT * FROM requests WHERE id=?", (cursor.lastrowid,)).fetchone()
            run_dir = self.state_dir / "cli-runs" / time.strftime("%Y%m%d-%H%M%S")
            result = asyncio.run(self.review(request, CliContext(run_dir), cfg, db, force_draft=True))
        finally:
            db.close()
        draft = result.extra.get("draft")
        if result.status == "done" and draft and Path(draft).exists():
            print(Path(draft).read_text())
        print(f"{result.status}: {result.error or result.summary or ''}".strip())
        return 0 if result.status == "done" else 1
