"""background: hand the hub a task to run while you do something else (`!bg`, `agents bg`, or the agents-hub
MCP tool from any Claude or Codex session). The answer lands in the bot's DM.

A task runs read-only straight away, in the folder it was sent from when that is one of the repos. A task
that may change code or tickets (`--changes`) only posts a "Go ahead" button; nothing runs until Aakif
taps it, and then it runs in the hub's act mode (worktrees, draft PRs, Linear writes; never merging).
Answers can end in `PROPOSED:` lines, which become buttons the same way.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ...core import agent
from ...core.plugin import Check, JobContext, Plugin
from ...core.store import Job, JobResult
from ...core.voice import turn_reminder
from ..slack_ask.plugin import button_label, split_proposals

INSTRUCTIONS = """\
You are running a background task Aakif handed you from his laptop (from a Claude or Codex session, the \
terminal, or Slack). Nobody is watching and nobody can answer a question, so do the task and report. \
Your tools are read-only: you cannot edit files, commit, push, open PRs, or change tickets. When the task \
needs one of those, do every read it takes, then end your answer with one line per change (at most 3): \
`PROPOSED: <the change, one short imperative line>`; he gets a button for each. Never propose merging, \
approving, deploying, az, or touching production data. His own Claude Code and Codex sessions on this \
Mac are readable with `python3 ~/.agents/hub/core/sessions.py list` and `... show <id> --tail 40`, for a \
task about what a session did or decided. Your last message is posted to his Slack DM as it is: lead with \
the answer, keep it short, link what you cite (PRs, tickets, files with line numbers).
"""


class Background(Plugin):
    name = "background"
    title = "Background tasks"
    priority = 55
    max_concurrent = 1

    def cwd_for(self, requested: str | None) -> Path:
        """The folder the task came from when it is inside one of the hub's repos or worktrees, else the default."""
        cfg = self.hub.cfg.get("agent", {})
        default = Path(cfg.get("workdir", "~/dev/envoyai-backend")).expanduser()
        if not requested:
            return default
        path = Path(requested).expanduser().resolve()
        allowed = [Path(r).expanduser().resolve() for r in cfg.get("repos", [])]
        allowed.append((Path.home() / "dev" / ".claude" / "worktrees").resolve())
        return path if path.is_dir() and any(path == a or a in path.parents for a in allowed) else default

    # ------------------------------------------------------------------ queueing

    def cmd_bg(self, args: list[str]) -> str:
        changes, cwd, source = False, None, ""
        words: list[str] = []
        i = 0
        while i < len(args):
            if args[i] == "--changes":
                changes = True
            elif args[i] in ("--cwd", "--from") and i + 1 < len(args):
                if args[i] == "--cwd":
                    cwd = args[i + 1]
                else:
                    source = args[i + 1]
                i += 1
            else:
                words.append(args[i])
            i += 1
        task = " ".join(words).strip()
        if not task:
            return "what should I do? `!bg <task>`"
        title = task if len(task) <= 90 else task[:89] + "…"
        job = self.enqueue(title, {"task": task, "changes": changes, "cwd": cwd, "source": source})
        if job is None:
            return "that one is already queued"
        if changes:
            return f"`{job.id}` queued; it may change things, so it'll ask you in Slack before doing anything"
        return f"`{job.id}` queued; the answer lands in your DM with the bot"

    def aliases(self) -> dict[str, Callable[[list[str]], str]]:
        return {"bg": self.cmd_bg}

    # ------------------------------------------------------------------ running

    def handle(self, job: Job, ctx: JobContext) -> JobResult:
        payload = job.payload
        task, cwd = payload["task"], self.cwd_for(payload.get("cwd"))
        slack = self.hub.slack
        header = f"*Background task* `{job.id}`" + (f" _(from {payload['source']})_" if payload.get("source") else "")
        if payload.get("changes"):
            button = self.offer("agent", "Go ahead", {"instruction": task, "cwd": str(cwd)}, style="primary",
                                confirm=task[:300])
            skip = self.offer("skip", "Skip", {"task": task})
            text = f"{header}\nThis one may change code or tickets, so it waits for your OK:\n> {task}"
            if slack is None or not slack.bot_dm:
                return JobResult("failed", error="Slack isn't connected, so it can't ask for the OK")
            slack.post_markdown(slack.bot_dm, text, blocks=self.hub.actions.blocks(text, [button, skip]))
            return JobResult(summary="waiting for your OK in Slack")
        cfg = self.hub.cfg.get("agent", {})
        reminder = turn_reminder()
        prompt = f"The task:\n{task}" + (f"\n\n{reminder}" if reminder else "")
        result = ctx.run_agent(agent.AgentTask(
            prompt=prompt, cwd=cwd, system=INSTRUCTIONS, timeout=int(self.config.get("timeout_seconds", 1800)),
            mcp_servers=list(cfg.get("mcp_servers", [])) + ["linear", "datadog"], add_dirs=list(cfg.get("add_dirs", [])),
            claude_allowed_tools=self.config.get("claude_allowed_tools", []),
            claude_settings=Path(__file__).resolve().parents[1] / "slack_ask" / "claude-settings.json",
        ))
        if result.error:
            text, proposals = f"{header}\nCouldn't finish: {result.error}", []
        else:
            answer, proposals = split_proposals(result.text)
            text = f"{header}\n{answer}"
        resume = result.resume_command(str(cwd).replace(str(Path.home()), "~"))
        if resume:
            text += f"\n\n_pick it up on the laptop:_ `{resume}`"
        link = None
        if slack is not None and slack.bot_dm:
            stamps = slack.post_markdown(slack.bot_dm, text)
            if proposals:
                buttons = [self.offer("agent", button_label(p), {"instruction": p + "\n\nThis is the change you "
                                      "proposed for the background task above; carry it out.", "session": result.session,
                                      "cwd": str(cwd)}, confirm=p[:300]) for p in proposals]
                note = "Want me to do any of these?"
                slack.post_markdown(slack.bot_dm, note, stamps[-1] if stamps else None,
                                    blocks=self.hub.actions.blocks(note, buttons))
            link = slack.permalink(slack.bot_dm, stamps[0]) if stamps else None
        first = next((line for line in (result.text or "").splitlines() if line.strip()), "")
        return JobResult("failed" if result.error else "done", summary=first[:300] or None, link=link,
                         error=result.error, engine=result.engine, session=result.session, tokens=result.tokens,
                         cost=result.cost_usd)

    def act(self, name: str, args: dict[str, Any], job: Job, ctx: JobContext) -> JobResult:
        if name == "skip":
            reply = job.payload.get("reply") or {}
            if reply.get("channel") and self.hub.slack is not None:
                self.hub.slack.post(reply["channel"], "skipped", reply.get("thread_ts"))
            return JobResult(summary="skipped")
        return super().act(name, args, job, ctx)

    def checks(self) -> list[Check]:
        return [Check("read-only Claude allow list", bool(self.config.get("claude_allowed_tools")))]
