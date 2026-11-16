import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from hub import mcp_server
from hub.core import agent
from hub.core.hub import Hub
from hub.plugins.background.plugin import Background
from hub.plugins.reminders.plugin import Reminders, parse_when


class FakeSlack:
    bot_name, bot_aliases, bot_user_id, bot_dm, connected, dm_as_user, error = "Dot", [], "UBOT", "D1", True, True, None

    def __init__(self):
        self.posts = []
        self.web = mock.Mock()

    def post(self, channel, text, thread_ts=None, **kw):
        self.posts.append((channel, text, thread_ts, None))
        return "1.1"

    def post_markdown(self, channel, text, thread_ts=None, blocks=None, **kw):
        self.posts.append((channel, text, thread_ts, blocks))
        return ["2.%d" % len(self.posts)]

    def dm_owner(self, text, thread_ts=None):
        return self.post("D1", text, thread_ts)

    def permalink(self, channel, ts, **kw):
        return f"https://slack/{ts}"

    def subscribe_interactive(self, handler):
        pass


def make_hub(root):
    hub = Hub({"owner": {"slack_user_id": "UOWNER"}, "plugins": {}, "agent": {"workdir": root, "repos": [root]}},
              state_dir=Path(root) / "state", with_slack=False)
    hub.slack = FakeSlack()
    hub.plugins["reminders"] = Reminders(hub, {})
    hub.plugins["background"] = Background(hub, {"claude_allowed_tools": ["Read"]})
    return hub


class Times(unittest.TestCase):
    def test_times_people_type(self):
        now = datetime(2026, 11, 12, 14, 0).astimezone()
        def at(text):
            parsed = parse_when(text.split(), now)
            return datetime.fromtimestamp(parsed[0]).strftime("%a %H:%M") if parsed else None
        self.assertEqual(at("in 2h x"), "Thu 16:00")
        self.assertEqual(at("at 3pm x"), "Thu 15:00")
        self.assertEqual(at("at 5 x"), "Thu 17:00")
        self.assertEqual(at("at 9 x"), "Fri 09:00")
        self.assertEqual(at("tomorrow x"), "Fri 09:00")
        self.assertEqual(at("monday 2pm x"), "Mon 14:00")
        self.assertIsNone(at("3 things to do"))


class RemindersAndBackground(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.hub = make_hub(self.root)

    def test_remind_fires_once_with_snooze_buttons(self):
        reply = self.hub.command(["remind", "--when", "in 5 minutes", "check the deploy", "--from", "claude-code in ~/x"])
        self.assertIn("check the deploy", reply)
        self.assertIn("check the deploy", self.hub.command(["reminders"]))
        reminders = self.hub.plugins["reminders"]
        self.assertEqual(reminders.fire_due(), [], "not due yet")
        fired = reminders.fire_due(now=time.time() + 400)
        self.assertEqual(len(fired), 1)
        text, blocks = self.hub.slack.posts[-1][1], self.hub.slack.posts[-1][3]
        self.assertIn("⏰ check the deploy", text)
        self.assertIn("claude-code in ~/x", text)
        self.assertIn("Snooze 1h", json.dumps(blocks))
        self.assertEqual(reminders.fire_due(now=time.time() + 400), [], "never twice")

    def test_snooze_and_unremind(self):
        reminders = self.hub.plugins["reminders"]
        job = self.hub.store.enqueue("reminders", "snooze", {"reply": {"channel": "D1", "thread_ts": "9.9"}})
        reminders.act("snooze", {"text": "standup", "seconds": 3600}, job, mock.Mock())
        rid = next(iter(reminders.pending()))
        self.assertIn("dropped", self.hub.command(["unremind", rid]))
        self.assertIn("couldn't read", self.hub.command(["remind", "--when", "whenever", "x"]))

    def test_read_only_task_answers_in_dm_with_buttons_for_proposals(self):
        reply = self.hub.command(["bg", "--cwd", self.root, "--from", "codex in ~/x", "why is CI red on 3105"])
        self.assertIn("queued", reply)
        job = self.hub.store.jobs(plugin="background")[0]
        seen = {}

        def fake_run(task):
            seen["task"] = task
            return agent.AgentResult("codex", text="mypy fails on x.py:3\nPROPOSED: fix the type in x.py and push",
                                     session="codex:s1")

        result = self.hub.plugins["background"].handle(job, mock.Mock(run_agent=fake_run))
        self.assertEqual(seen["task"].mode, "read-only")
        self.assertEqual(str(seen["task"].cwd), str(Path(self.root).resolve()))
        answer = self.hub.slack.posts[0][1]
        self.assertIn("mypy fails", answer)
        self.assertNotIn("PROPOSED", answer)
        self.assertIn("fix the type", json.dumps(self.hub.slack.posts[1][3]))
        self.assertEqual((result.status, result.session), ("done", "codex:s1"))

    def test_a_task_that_changes_things_only_asks(self):
        self.hub.command(["bg", "--changes", "open a PR bumping the timeout"])
        job = self.hub.store.jobs(plugin="background")[0]
        run = mock.Mock()
        result = self.hub.plugins["background"].handle(job, mock.Mock(run_agent=run))
        run.assert_not_called()
        self.assertIn("Go ahead", json.dumps(self.hub.slack.posts[-1][3]))
        self.assertEqual(result.summary, "waiting for your OK in Slack")

    def test_a_cwd_outside_the_repos_falls_back_to_the_default(self):
        self.assertEqual(self.hub.plugins["background"].cwd_for("/etc"), Path(self.root))


class McpServer(unittest.TestCase):
    def test_tools_list_and_calls_go_to_the_hub(self):
        server = mcp_server.Server()
        init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-code"}}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "agents-hub")
        names = [t["name"] for t in server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
        self.assertEqual(names, ["slack_me", "remind_me", "run_in_background", "oncall_notes", "oncall_note",
                                 "hub_status", "hub_job"])
        with mock.patch.object(mcp_server, "hub_command", return_value="ok") as command:
            out = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                 "params": {"name": "run_in_background", "arguments": {"task": "t", "allow_changes": True}}})
            words = command.call_args[0][0]
        self.assertEqual(out["result"]["content"][0]["text"], "ok")
        self.assertEqual((words[0], words[-2:], words[words.index("--from") + 1].split(" in ")[0]),
                         ("bg", ["--changes", "t"], "claude-code"))
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))


if __name__ == "__main__":
    unittest.main()
