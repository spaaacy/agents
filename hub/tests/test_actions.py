import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hub.core import actions, agent, engine
from hub.core.hub import Hub
from hub.core.plugin import Plugin
from hub.core.store import JobResult


class Fixer(Plugin):
    name = "fixer"
    title = "Fixer"

    def handle(self, job, ctx):
        return JobResult()

    def act(self, name, args, job, ctx):
        if name == "post":
            return JobResult(summary=f"posted {args['pr']}")
        return super().act(name, args, job, ctx)


class FakeSlack:
    bot_name, bot_aliases, bot_user_id, connected, dm_as_user, error = "Dot", [], "UBOT", True, True, None

    def __init__(self):
        self.posts, self.updates, self.handlers = [], [], []
        self.web = mock.Mock()
        self.web.chat_update.side_effect = lambda **kw: self.updates.append(kw)

    def post(self, channel, text, thread_ts=None, **kw):
        self.posts.append((channel, text, thread_ts))
        return "1.1"

    def post_markdown(self, channel, text, thread_ts=None, **kw):
        self.posts.append((channel, text, thread_ts))
        return ["2.2"]

    def permalink(self, channel, ts, **kw):
        return f"https://slack/{channel}/{ts}"

    def subscribe_interactive(self, handler):
        self.handlers.append(handler)


def tap(token, user="UOWNER", blocks=None):
    return {"type": "block_actions", "user": {"id": user}, "container": {"type": "message"},
            "channel": {"id": "D1"}, "message": {"ts": "9.9", "text": "report", "blocks": blocks or []},
            "actions": [{"action_id": f"act:{token}", "value": token}]}


class Buttons(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.hub = Hub({"owner": {"slack_user_id": "UOWNER"}, "plugins": {}}, state_dir=Path(tmp.name), with_slack=False)
        self.hub.slack = FakeSlack()
        self.hub.plugins["fixer"] = Fixer(self.hub, {})

    def test_a_tap_queues_the_action_once_and_marks_the_message(self):
        button = self.hub.plugins["fixer"].offer("post", "Post the review", {"pr": 5}, style="primary", confirm="sure?")
        token = button["value"]
        blocks = actions.Actions.blocks("the draft", [button, self.hub.plugins["fixer"].offer("skip", "Skip")])
        job_id = self.hub.actions.run(token, tap(token, blocks=blocks))
        job = self.hub.store.get(job_id)
        self.assertEqual((job.plugin, job.payload["action"], job.payload["args"], job.payload["reply"]),
                         ("fixer", "post", {"pr": 5}, {"channel": "D1", "thread_ts": "9.9"}))
        updated = self.hub.slack.updates[-1]["blocks"]
        self.assertEqual([e["text"]["text"] for b in updated if b["type"] == "actions" for e in b["elements"]], ["Skip"])
        self.assertIn(job_id, json.dumps(updated))
        self.assertIsNone(self.hub.actions.run(token, tap(token)), "a second tap starts nothing")
        self.assertIn("Already done", self.hub.slack.posts[-1][1])

    def test_only_the_owner_and_never_an_expired_button(self):
        token = self.hub.plugins["fixer"].offer("post", "Post", {"pr": 1})["value"]
        self.hub.actions.on_action(tap(token, user="SOMEONE"))
        self.assertEqual(self.hub.store.jobs(), [])
        record = self.hub.store.get_value("actions", token)
        record["created"] = time.time() - actions.TTL_SECONDS - 1
        self.hub.store.set_value("actions", token, record)
        self.assertIsNone(self.hub.actions.run(token, tap(token)))
        self.assertIn("expired", self.hub.slack.posts[-1][1])

    def test_the_scheduler_sends_an_action_job_to_act(self):
        self.hub.scheduler.start()
        self.addCleanup(self.hub.stopping.set)
        token = self.hub.plugins["fixer"].offer("post", "Post", {"pr": 7})["value"]
        job_id = self.hub.actions.run(token, tap(token))
        for _ in range(100):
            job = self.hub.store.get(job_id)
            if job.status == "done":
                break
            time.sleep(0.05)
        self.assertEqual(job.summary, "posted 7")

    def test_agent_action_runs_in_act_mode_resuming_the_session_and_replies(self):
        seen = {}

        def fake_run(task):
            seen["task"] = task
            return agent.AgentResult("codex", text="Opened PR #9\nhttps://github.com/x/9", session="codex:s2")

        job = self.hub.store.enqueue("fixer", "do it", {"action": "agent", "reply": {"channel": "D1", "thread_ts": "9.9"}})
        ctx = mock.Mock(run_agent=fake_run)
        result = self.hub.plugins["fixer"].act("agent", {"instruction": "open the PR", "session": "codex:s1"}, job, ctx)
        task = seen["task"]
        self.assertEqual((task.mode, task.claude_settings), ("act", agent.ACT_CLAUDE_SETTINGS))
        self.assertTrue(task.prompt.startswith(agent.ACT_PREAMBLE) and task.prompt.endswith("open the PR"))
        self.assertIn("linear", task.mcp_servers)
        self.assertIn("Opened PR #9", self.hub.slack.posts[-1][1])
        self.assertEqual((result.status, result.session, result.summary), ("done", "codex:s2", "Opened PR #9"))


class ActHome(unittest.TestCase):
    def test_act_home_writes_only_where_allowed_and_has_linear_writes_and_agents_md(self):
        with tempfile.TemporaryDirectory() as root:
            user = Path(root) / "user"
            user.mkdir()
            (user / "auth.json").write_text("{}")
            (user / "config.toml").write_text('[mcp_servers.linear]\nurl = "https://mcp.linear.app/mcp"\n')
            agents_md = Path(root) / "AGENTS.md"
            agents_md.write_text("rules")
            import tomllib
            with mock.patch.object(engine, "USER_CODEX_HOME", user), mock.patch.object(engine, "AGENTS_MD", agents_md):
                home = engine.prepare_codex_home(["linear"], home=Path(root) / "act", choice=engine.DEFAULT, act=True,
                                                 write_dirs=[Path(root) / "worktrees"])
            config = tomllib.loads((home / "config.toml").read_text())
            fs = config["permissions"]["background"]["filesystem"]
            self.assertEqual(fs, {":root": "read", str(Path(root) / "worktrees"): "write"})
            self.assertIn("save_issue", config["mcp_servers"]["linear"]["enabled_tools"])
            self.assertEqual(config["mcp_servers"]["linear"]["tools"]["save_issue"], {"approval_mode": "approve"})
            self.assertEqual((home / "AGENTS.md").read_text(), "rules")
            self.assertEqual([p.name for p in (home / "rules").iterdir()], ["act.rules"])

    def test_act_runs_route_git_hooks_through_the_push_guard(self):
        env = engine.act_git_env()
        self.assertEqual((env["GIT_CONFIG_KEY_0"], Path(env["GIT_CONFIG_VALUE_0"]).name), ("core.hooksPath", "git-hooks"))
        self.assertTrue((engine.GIT_HOOKS / "pre-push").exists())


if __name__ == "__main__":
    unittest.main()
