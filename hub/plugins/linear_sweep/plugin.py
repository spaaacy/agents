"""linear_sweep: move Aakif's Linear tickets to the state their code reached, after every release.

The source (never an agent) checks every 10 minutes for promotion PRs newly
merged into `staging` or `production` in the app repos, and once a day at
09:30 local time. Either one queues a sweep job. The first start only records
the newest promotion per repo and branch, so turning the plugin on doesn't
sweep for releases that shipped before it.

A sweep job runs the /linear-sweep skill unattended in the hub's act mode
(core/agent.py): phase 1 then phase 2, his own tickets only, state moves only,
forward only. Aakif decided (2026-11-12) that it moves them without asking and
DMs him afterwards; the skill already allows moving his own tickets' state. A
run that moved nothing posts nothing unless it was started by hand or failed.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from ...core import agent, engine
from ...core.plugin import Check, JobContext, Plugin
from ...core.store import Job, JobResult

SKILL_FILE = Path.home() / ".agents" / "skills" / "linear-sweep" / "SKILL.md"
MOVED_LINE = re.compile(r"^\s*MOVED:\s*(\d+)\s*$", re.I)
EXTRA_CLAUDE_TOOLS = ["Bash(npm view *)", "Bash(gh auth status *)"]

PREAMBLE = """\
This is Aakif's scheduled Linear sweep, run by his agents hub with nobody \
watching. He approved it running on its own (2026-11-12): after each release \
it moves his tickets to the state their code reached and tells him what \
moved. Nobody can answer a question, so do the sweep, then report.
- Run the skill below unattended: phase 1, then phase 2 straight away, with \
no question. His own tickets only: never `all`, never another person's ticket.
- The only ticket edit is moving a state forward (Merged, Staging, Done). \
Never move to Canceled or Duplicate, never move a ticket back, and never \
change assignee, project, cycle, labels, title or description, comment, or \
create a ticket.
- `git fetch` in the ~/dev clones is allowed (their git data is writable \
here); nothing else in a checkout is. Never merge, push, or run az.
- Follow ~/.agents/AGENTS.md and the skill's rules for deciding each state.

Your last message is posted to his Slack DM as it is, so it is plain and \
short. Its FIRST line is exactly `MOVED: <n>`, the number of tickets you \
moved (0 when none). Then one line per ticket moved: key, from → to, and the \
evidence in a few words (e.g. "ENG-2470 Merged → Done, #3079 in the 11/12 \
production promotion"). Then one line: "already right: <n>, skipped: <n> \
(<short reasons>)". When nothing moved, the line after `MOVED: 0` says so in \
one sentence.

What started this sweep: {trigger}
"""


def skill_text(path: Path = SKILL_FILE) -> str:
    """The /linear-sweep skill, frontmatter removed and its arguments empty (his tickets, default scope)."""
    text = path.read_text()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    return text.replace("$ARGUMENTS", "").strip()


def parse_summary(text: str) -> tuple[int | None, str]:
    """(tickets moved, the summary without its MOVED line); None when the line is missing."""
    lines = text.strip().splitlines()
    for index, line in enumerate(lines[:3]):
        match = MOVED_LINE.match(line)
        if match:
            rest = "\n".join(lines[:index] + lines[index + 1:]).strip()
            return int(match[1]), rest
    return None, text.strip()


def describe_trigger(payload: dict[str, Any]) -> str:
    trigger = payload.get("trigger", "manual")
    if trigger == "release":
        promos = ", ".join(f"{p['repo'].split('/')[-1]} #{p['number']} into {p['branch']}"
                           for p in payload.get("promotions", []))
        return f"new promotion(s) merged: {promos}"
    if trigger == "daily":
        return "the daily 09:30 sweep (catches tickets that only reached Merged)"
    return "Aakif asked for a sweep by hand"


class LinearSweep(Plugin):
    name = "linear_sweep"
    title = "Linear sweep"
    priority = 40
    max_concurrent = 1

    def __init__(self, hub: Any, config: dict[str, Any]) -> None:
        super().__init__(hub, config)
        self.last_error: str | None = None

    # ------------------------------------------------------------------ the source

    def start(self) -> None:
        self.spawn(self._loop, "poll")

    def _loop(self) -> None:
        while not self.hub.stopping.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - one bad poll waits for the next
                self.log.exception("promotion poll failed")
                self.last_error = f"{type(exc).__name__}: {exc}"
            self.daily_once()
            self.hub.stopping.wait(int(self.config.get("poll_seconds", 600)))

    def merged_promotions(self, repo: str, branch: str) -> list[dict[str, Any]]:
        """The latest promotion PRs merged into ``branch``, newest first. Raises when gh fails."""
        out = subprocess.run(
            [self.config.get("gh", "gh"), "pr", "list", "-R", repo, "--base", branch, "--state", "merged",
             "--limit", "5", "--json", "number,mergedAt,title"],
            capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError(f"gh pr list {repo} {branch}: {out.stderr.strip()[-300:]}")
        prs = [pr for pr in json.loads(out.stdout or "[]") if pr.get("mergedAt")]
        return sorted(prs, key=lambda pr: pr["mergedAt"], reverse=True)

    def poll_once(self) -> Job | None:
        """Record the newest promotion per repo and branch; queue one sweep for everything new since last time."""
        seen: dict[str, Any] = self.get("seen", {}) or {}
        initialized = bool(self.get("initialized"))
        new: list[dict[str, Any]] = []
        errors = []
        for repo in self.config.get("repos", []):
            for branch in self.config.get("branches", ["staging", "production"]):
                key = f"{repo}:{branch}"
                try:
                    prs = self.merged_promotions(repo, branch)
                except Exception as exc:  # noqa: BLE001 - keep the others; this key stays as it was
                    errors.append(str(exc))
                    continue
                if not prs:
                    continue
                last = (seen.get(key) or {}).get("merged_at")
                if initialized and last:
                    new += [{"repo": repo, "branch": branch, "number": pr["number"], "merged_at": pr["mergedAt"],
                             "title": pr.get("title", "")} for pr in prs if pr["mergedAt"] > last]
                newest = prs[0]
                seen[key] = {"merged_at": newest["mergedAt"], "number": newest["number"]}
        self.put("seen", seen)
        if not initialized:
            self.put("initialized", True)
        self.last_error = "; ".join(errors) or None
        self.put("last_poll", {"at": time.time(), "error": self.last_error})
        if not new:
            return None
        new.sort(key=lambda p: (p["repo"], p["branch"], p["number"]))
        dedupe = "promo:" + "+".join(f"{p['repo'].split('/')[-1]}:{p['number']}" for p in new)
        titles = ", ".join(f"{p['repo'].split('/')[-1]} #{p['number']}" for p in new)
        return self.enqueue(f"sweep after {titles}", {"trigger": "release", "promotions": new}, dedupe=dedupe)

    def daily_once(self, now: float | None = None) -> Job | None:
        """The 09:30 local sweep, once a day; a day the hub starts after 09:30 still gets it."""
        now = time.time() if now is None else now
        local = time.localtime(now)
        hour, minute = (int(x) for x in str(self.config.get("daily_at", "09:30")).split(":"))
        today = time.strftime("%Y-%m-%d", local)
        if (local.tm_hour, local.tm_min) < (hour, minute) or self.get("daily_done") == today:
            return None
        self.put("daily_done", today)
        return self.enqueue(f"daily sweep {today}", {"trigger": "daily"}, dedupe=f"daily:{today}")

    # ------------------------------------------------------------------ the job

    def build_task(self, payload: dict[str, Any], run_dir: Path | None = None) -> agent.AgentTask:
        cfg = self.hub.cfg.get("agent", {})
        prompt = (PREAMBLE.format(trigger=describe_trigger(payload))
                  + "\n<skill name=\"linear-sweep\">\n" + skill_text() + "\n</skill>\n")
        return agent.AgentTask(
            prompt=prompt, cwd=Path(cfg.get("workdir", "~/dev/envoyai-backend")).expanduser(), mode="act",
            timeout=int(self.config.get("timeout_seconds", 1800)), run_dir=run_dir,
            mcp_servers=list(cfg.get("mcp_servers", [])) + ["linear"],
            add_dirs=list(cfg.get("add_dirs", [])), repos=list(cfg.get("repos", [])),
            claude_allowed_tools=agent.ACT_CLAUDE_TOOLS + EXTRA_CLAUDE_TOOLS,
            claude_settings=agent.ACT_CLAUDE_SETTINGS,
        )

    def handle(self, job: Job, ctx: JobContext) -> JobResult:
        ctx.note("sweeping…")
        result = ctx.run_agent(self.build_task(job.payload))
        moved, summary = parse_summary(result.text) if not result.error else (None, "")
        manual = job.payload.get("trigger") == "manual"
        if result.error:
            text = f"Linear sweep failed: {result.error}"
        elif moved is None:
            text = "Linear sweep finished, but its report didn't say how many tickets moved:\n\n" + summary
        else:
            text = summary or ("Nothing to move." if not moved else "")
        line = next((l for l in text.splitlines() if l.strip()), "")
        self.put("last_sweep", {"at": time.time(), "moved": moved, "line": line[:300], "job": job.id})
        should_post = bool(result.error) or manual or moved is None or bool(moved)
        link = None
        slack = self.hub.slack
        if should_post and slack is not None and slack.bot_dm:
            resume = result.resume_command()
            body = f"*Linear sweep* ({describe_trigger(job.payload)})\n\n{text}"
            if resume and (result.error or moved is None):
                body += f"\n\n_to pick it up on the laptop:_ `{resume}`"
            posted = slack.post_markdown(slack.bot_dm, body)
            link = slack.permalink(slack.bot_dm, posted[0]) if posted else None
        summary_line = f"moved {moved}: {line}" if moved else (line or "nothing moved")
        return JobResult(status="failed" if result.error else "done", summary=summary_line[:300], link=link,
                         error=result.error, engine=result.engine, session=result.session, tokens=result.tokens,
                         cost=result.cost_usd, extra={"moved": moved})

    # ------------------------------------------------------------------ controls

    def commands(self) -> dict[str, Callable[[list[str]], str]]:
        def run(_: list[str]) -> str:
            job = self.enqueue("sweep by hand", {"trigger": "manual"}, dedupe=f"manual:{int(time.time())}")
            return f"queued `{job.id}`; I'll DM what moved" if job else "a sweep is already queued"
        return {"run": run}

    def cli(self) -> dict[str, Callable[[list[str]], int]]:
        def run(_: list[str]) -> int:
            run_dir = self.state_dir / "cli-runs" / time.strftime("%Y%m%d-%H%M%S")
            result = agent.run(self.build_task({"trigger": "manual"}, run_dir))
            if result.error:
                print(f"sweep failed: {result.error}")
                return 1
            moved, summary = parse_summary(result.text)
            print(summary or f"moved {moved}")
            return 0
        return {"run": run}

    def status(self) -> dict[str, Any]:
        last_poll = self.get("last_poll") or {}
        last_sweep = self.get("last_sweep") or {}
        seen = self.get("seen") or {}
        return {
            "last_poll_at": last_poll.get("at"),
            "last_error": last_poll.get("error"),
            "last_sweep_at": last_sweep.get("at"),
            "last_sweep": last_sweep.get("line"),
            "newest_promotions": ", ".join(f"{k.split('/')[-1]} #{v.get('number')}" for k, v in seen.items()) or None,
        }

    def checks(self) -> list[Check]:
        out = []
        try:
            auth = subprocess.run([self.config.get("gh", "gh"), "auth", "status"], capture_output=True, text=True,
                                  timeout=30)
            out.append(Check("gh signed in", auth.returncode == 0, (auth.stdout + auth.stderr).strip().splitlines()[0]
                             if (auth.stdout + auth.stderr).strip() else ""))
        except Exception as exc:  # noqa: BLE001
            out.append(Check("gh signed in", False, str(exc)))
        out.append(Check("Linear MCP in ~/.codex/config.toml", bool(engine.mcp_servers(["linear"]))))
        out.append(Check("linear-sweep skill", SKILL_FILE.exists(), str(SKILL_FILE)))
        return out
