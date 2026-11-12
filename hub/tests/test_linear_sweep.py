import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hub.core import agent
from hub.core.hub import Hub
from hub.plugins.linear_sweep import plugin as sweep


class FakeSlack:
    bot_dm = "DBOT"

    def __init__(self):
        self.posts = []

    def post_markdown(self, channel, text, thread_ts=None, **_):
        self.posts.append((channel, text))
        return ["1.0"]

    def permalink(self, channel, ts, **_):
        return f"https://slack/{channel}/{ts}"


class FakeCtx:
    def __init__(self, result):
        self.result = result
        self.tasks = []

    def note(self, _):
        pass

    def run_agent(self, task):
        self.tasks.append(task)
        return self.result


def pr(number, merged_at):
    return {"number": number, "mergedAt": merged_at, "title": f"promote {number}"}


class Sweep(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = {"owner": {"slack_user_id": "U1"}, "plugins": {}, "agent": {"workdir": tmp.name}}
        self.hub = Hub(cfg, state_dir=Path(tmp.name), with_slack=False)
        self.plugin = sweep.LinearSweep(self.hub, {"repos": ["envoy-ai/envoyai-backend"], "branches": ["production"],
                                                   "daily_at": "09:30"})
        self.prs = {"envoy-ai/envoyai-backend:production": [pr(10, "2026-11-10T20:00:00Z")]}
        patcher = mock.patch.object(self.plugin, "merged_promotions",
                                    side_effect=lambda repo, branch: sorted(self.prs[f"{repo}:{branch}"],
                                                                            key=lambda p: p["mergedAt"], reverse=True))
        patcher.start()
        self.addCleanup(patcher.stop)

    def queued(self):
        return self.hub.store.jobs(plugin="linear_sweep")

    def test_first_start_records_only_then_a_new_merge_queues_once(self):
        self.assertIsNone(self.plugin.poll_once())
        self.assertEqual(self.queued(), [])
        self.assertIsNone(self.plugin.poll_once(), "nothing new")
        self.prs["envoy-ai/envoyai-backend:production"].append(pr(11, "2026-11-12T19:00:00Z"))
        job = self.plugin.poll_once()
        self.assertEqual(job.payload["trigger"], "release")
        self.assertEqual([p["number"] for p in job.payload["promotions"]], [11])
        self.assertIsNone(self.plugin.poll_once(), "already recorded")
        self.assertEqual(len(self.queued()), 1)

    def test_two_new_merges_make_one_job_and_a_redelivery_is_deduped(self):
        self.plugin.poll_once()
        self.prs["envoy-ai/envoyai-backend:production"] += [pr(11, "2026-11-12T19:00:00Z"),
                                                             pr(12, "2026-11-12T21:00:00Z")]
        job = self.plugin.poll_once()
        self.assertEqual(sorted(p["number"] for p in job.payload["promotions"]), [11, 12])
        # The same promotions seen again (the seen marker lost) dedupe to the existing job.
        self.plugin.put("seen", {"envoy-ai/envoyai-backend:production": {"merged_at": "2026-11-10T20:00:00Z"}})
        self.assertIsNone(self.plugin.poll_once())
        self.assertEqual(len(self.queued()), 1)

    def test_daily_sweep_once_after_its_time(self):
        before = time.mktime(time.strptime("2026-11-12 09:00", "%Y-%m-%d %H:%M"))
        after = time.mktime(time.strptime("2026-11-12 09:45", "%Y-%m-%d %H:%M"))
        self.assertIsNone(self.plugin.daily_once(before))
        job = self.plugin.daily_once(after)
        self.assertEqual(job.payload["trigger"], "daily")
        self.assertIsNone(self.plugin.daily_once(after + 600))
        self.assertIsNotNone(self.plugin.daily_once(after + 86400))

    def test_prompt_inlines_the_skill_and_the_unattended_rules(self):
        task = self.plugin.build_task({"trigger": "daily"})
        self.assertEqual(task.mode, "act")
        self.assertIn("linear", task.mcp_servers)
        self.assertIn('<skill name="linear-sweep">', task.prompt)
        self.assertIn("Phase 2", task.prompt)
        self.assertIn("MOVED: <n>", task.prompt)
        self.assertIn("never `all`", task.prompt)
        self.assertNotIn("$ARGUMENTS", task.prompt)
        self.assertIn("Bash(npm view *)", task.claude_allowed_tools)

    def test_posts_only_when_something_moved_or_by_hand_or_failed(self):
        slack = FakeSlack()
        self.hub.slack = slack
        quiet = agent.AgentResult("codex", text="MOVED: 0\nNothing to move.\nalready right: 3, skipped: 0",
                                  session="codex:s1")
        job = self.hub.store.enqueue("linear_sweep", "daily", {"trigger": "daily"})
        result = self.plugin.handle(job, FakeCtx(quiet))
        self.assertEqual(slack.posts, [])
        self.assertEqual((result.status, result.extra["moved"]), ("done", 0))

        moved = agent.AgentResult("codex", text="MOVED: 2\nENG-1 Merged → Done, #5 in production\n"
                                                "ENG-2 In Review → Merged\nalready right: 1, skipped: 0")
        result = self.plugin.handle(job, FakeCtx(moved))
        self.assertEqual(len(slack.posts), 1)
        self.assertIn("ENG-1 Merged → Done", slack.posts[-1][1])
        self.assertNotIn("MOVED:", slack.posts[-1][1])
        self.assertTrue(result.link)

        manual = self.hub.store.enqueue("linear_sweep", "by hand", {"trigger": "manual"})
        self.plugin.handle(manual, FakeCtx(quiet))
        self.assertEqual(len(slack.posts), 2)

        failed = agent.AgentResult("codex", error="codex exited 1: boom")
        result = self.plugin.handle(job, FakeCtx(failed))
        self.assertEqual((result.status, len(slack.posts)), ("failed", 3))
        self.assertIn("failed", slack.posts[-1][1])

    def test_missing_moved_line_is_reported(self):
        slack = FakeSlack()
        self.hub.slack = slack
        job = self.hub.store.enqueue("linear_sweep", "daily", {"trigger": "daily"})
        self.plugin.handle(job, FakeCtx(agent.AgentResult("codex", text="moved some stuff")))
        self.assertEqual(len(slack.posts), 1)
        self.assertEqual(sweep.parse_summary("x\nMOVED: 3\nENG-1 a → b"), (3, "x\nENG-1 a → b"))

    def test_run_command_queues_a_manual_sweep(self):
        reply = self.plugin.commands()["run"]([])
        self.assertIn("queued", reply)
        self.assertEqual(self.queued()[0].payload["trigger"], "manual")


if __name__ == "__main__":
    unittest.main()
