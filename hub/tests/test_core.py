import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from hub.core import agent, engine
from hub.core.hub import Hub
from hub.core.plugin import Plugin
from hub.core.store import JobResult, Store


class Store_(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Store(Path(self.dir.name) / "hub.sqlite3")

    def test_dedupe_makes_a_redelivered_event_harmless(self):
        self.assertIsNotNone(self.store.enqueue("a", "one", dedupe="x"))
        self.assertIsNone(self.store.enqueue("a", "again", dedupe="x"))

    def test_claim_takes_priority_then_age_and_respects_limits_and_pauses(self):
        low = self.store.enqueue("reviews", "low", priority=10)
        high = self.store.enqueue("pages", "high", priority=90)
        self.assertEqual(self.store.claim(set(), {"pages": 1, "reviews": 1}).id, high.id)
        self.assertEqual(self.store.claim(set(), {"pages": 1, "reviews": 1}).id, low.id)
        self.store.enqueue("reviews", "second review", priority=10)
        self.assertIsNone(self.store.claim(set(), {"reviews": 1}), "one review already running")
        self.store.enqueue("pages", "paused page", priority=90)
        self.store.finish(high.id, JobResult())
        self.assertIsNone(self.store.claim({"pages"}, {"pages": 1, "reviews": 1}))

    def test_finish_retry_cancel_and_recovery(self):
        job = self.store.enqueue("a", "t", {"k": 1})
        self.store.claim(set(), {"a": 1})
        self.assertIsNone(self.store.retry(job.id), "a running job is not retried")
        self.store.finish(job.id, JobResult(status="failed", error="boom", engine="codex", session="codex:s",
                                            extra={"more": 2}))
        done = self.store.get(job.id)
        self.assertEqual((done.status, done.error, done.engine, done.payload), ("failed", "boom", "codex", {"k": 1, "more": 2}))
        again = self.store.retry(job.id)
        self.assertEqual((again.status, again.attempt, again.retry_of, again.payload["k"]), ("queued", 2, job.id, 1))
        self.assertTrue(self.store.cancel(again.id))
        stuck = self.store.enqueue("a", "stuck")
        self.store.claim(set(), {"a": 1})
        self.assertEqual([j.id for j in self.store.recover_interrupted()], [stuck.id])
        self.assertEqual(self.store.get(stuck.id).status, "failed")

    def test_find_by_prefix_and_kv(self):
        job = self.store.enqueue("a", "t")
        self.assertEqual(self.store.find(job.id[:8]).id, job.id)
        self.store.set_value("a", "k", {"x": [1]})
        self.assertEqual(self.store.get_value("a", "k"), {"x": [1]})
        self.store.delete_value("a", "k")
        self.assertEqual(self.store.get_value("a", "k", "gone"), "gone")


class Echo(Plugin):
    name = "echo"
    title = "Echo"

    def handle(self, job, ctx):
        if job.payload.get("fail"):
            raise RuntimeError("nope")
        if job.payload.get("wait"):
            ctx.cancel.wait(5)
        return JobResult(summary=f"echo {job.payload.get('text')}")


class Scheduler_(unittest.TestCase):
    def make_hub(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict("sys.modules"):
            cfg = {"owner": {"slack_user_id": "U1"}, "plugins": {}, "scheduler": {"slots": 2}}
            hub = Hub(cfg, state_dir=Path(tmp.name), with_slack=False)
        hub.plugins["echo"] = Echo(hub, {})
        hub.scheduler.start()
        self.addCleanup(hub.stopping.set)
        return hub

    def wait_for(self, hub, job_id, status, seconds=5.0):
        deadline = time.time() + seconds
        while time.time() < deadline:
            job = hub.store.get(job_id)
            if job.status == status:
                return job
            time.sleep(0.05)
        self.fail(f"{job_id} never reached {status}: {hub.store.get(job_id).status}")

    def test_runs_records_failures_and_cancels(self):
        hub = self.make_hub()
        ok = hub.plugins["echo"].enqueue("say hi", {"text": "hi"})
        self.assertEqual(self.wait_for(hub, ok.id, "done").summary, "echo hi")
        bad = hub.plugins["echo"].enqueue("fail", {"fail": True})
        self.assertIn("nope", self.wait_for(hub, bad.id, "failed").error)
        slow = hub.plugins["echo"].enqueue("slow", {"wait": True})
        self.wait_for(hub, slow.id, "running")
        self.assertTrue(hub.scheduler.cancel(slow.id))
        self.assertEqual(self.wait_for(hub, slow.id, "cancelled").status, "cancelled")

    def test_paused_plugin_waits_and_controls_answer(self):
        hub = self.make_hub()
        self.assertIn("paused", hub.command(["pause", "echo"]))
        job = hub.plugins["echo"].enqueue("later", {"text": "x"})
        time.sleep(0.5)
        self.assertEqual(hub.store.get(job.id).status, "queued")
        hub.command(["resume", "echo"])
        self.wait_for(hub, job.id, "done")
        self.assertIn("echo", hub.command(["status"]))
        self.assertIn(job.id, hub.command(["jobs"]))
        self.assertIn("queued", hub.command(["retry", job.id]))
        self.assertIn("unknown command", hub.command(["frobnicate"]))


class Agent_(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(engine, "CHOICE_FILE", Path(self.dir.name) / "engine.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        reviews = mock.patch.object(engine, "REVIEW_CHOICE_FILE", Path(self.dir.name) / "engine-reviews.json")
        reviews.start()
        self.addCleanup(reviews.stop)

    def test_sessions_are_tagged_and_never_cross_engines(self):
        self.assertEqual(agent.split_session("codex:abc"), ("codex", "abc"))
        self.assertEqual(agent.split_session("old-claude-id"), ("claude", "old-claude-id"))
        self.assertEqual(agent.resumable("codex:abc", "codex"), "codex:abc")
        self.assertIsNone(agent.resumable("claude:abc", "codex"))
        result = agent.AgentResult("codex", session="codex:xyz")
        self.assertEqual(result.resume_command(), "cd ~/dev/envoyai-backend && codex resume xyz")

    def test_codex_read_only_turn_parses_events_and_structured_output(self):
        seen = {}

        def fake(cmd, stdin, cwd, timeout, on_line, env, cancel):
            seen.update(cmd=cmd, stdin=stdin, env=env)
            out = Path(cmd[cmd.index("-o") + 1])
            out.write_text('{"status": "ready"}')
            on_line('{"type":"thread.started","thread_id":"t9"}\n')
            on_line('{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":4}}\n')
            return 0, ""

        task = agent.AgentTask("question", Path(self.dir.name), system="be brief", schema={"type": "object"},
                               run_dir=Path(self.dir.name) / "run", env={"DD_API_KEY": "k"})
        with mock.patch.object(engine, "prepare_codex_home", return_value=Path(self.dir.name) / "home"):
            result = agent.run_codex(task, execute=fake)
        self.assertEqual((result.session, result.tokens, result.structured), ("codex:t9", 7, {"status": "ready"}))
        self.assertTrue(seen["stdin"].startswith("be brief"))
        self.assertEqual(seen["env"]["DD_API_KEY"], "k")
        self.assertTrue((Path(self.dir.name) / "run" / "events.jsonl").exists())

    def test_codex_resume_sends_no_instructions_again(self):
        def fake(cmd, stdin, cwd, timeout, on_line, env, cancel):
            Path(cmd[cmd.index("-o") + 1]).write_text("ok")
            self.assertEqual(cmd[1:3], ["exec", "resume"])
            self.assertEqual(stdin, "follow-up")
            return 0, ""

        task = agent.AgentTask("follow-up", Path(self.dir.name), system="rules", session="codex:t1",
                               run_dir=Path(self.dir.name))
        with mock.patch.object(engine, "prepare_codex_home", return_value=Path(self.dir.name)):
            self.assertEqual(agent.run_codex(task, execute=fake).text, "ok")

    def test_review_mode_uses_codex_auto_review_in_the_checkout(self):
        task = agent.AgentTask("review it", Path("/checkout"), mode="review")
        cmd = agent.codex_review_command(task, Path("/o"), None, None)
        self.assertIn("--approve-for-me", cmd)
        self.assertEqual(cmd[cmd.index("-C") + 1], "/checkout")
        self.assertIn("gpt-6.1-sol", cmd)

    def test_review_mode_runs_on_the_pinned_choice(self):
        choice = {"engine": "codex", "codex": {"model": "gpt-6-astra", "effort": "xhigh"},
                  "claude": {"model": "opus", "effort": "high"}}
        task = agent.AgentTask("review it", Path("/checkout"), mode="review", choice=choice)
        cmd = agent.codex_review_command(task, Path("/o"), None, None)
        self.assertEqual(cmd[cmd.index("-m") + 1], "gpt-6-astra")
        self.assertIn('model_reasoning_effort="xhigh"', cmd)
        claude = agent.claude_command(task, None, None)
        self.assertEqual(claude[claude.index("--model") + 1], "opus")
        self.assertEqual(claude[claude.index("--effort") + 1], "high")

    def test_review_commands_switch_only_the_reviews_setting(self):
        hub = Hub({"owner": {"slack_user_id": "U1"}, "plugins": {}}, state_dir=Path(self.dir.name) / "hub", with_slack=False)
        self.assertIn("PR reviews now run on codex (gpt-6.1-sol", hub.command(["model", "reviews", "sol"]))
        self.assertIn("PR reviews now run on claude", hub.command(["engine", "reviews", "claude"]))
        self.assertEqual((engine.engine(scope="reviews"), engine.engine()), ("claude", "codex"))
        self.assertIn("PR reviews run on claude", hub.command(["status"]))
        self.assertIn("PR reviews run on claude", hub.command(["effort", "reviews"]))

    def test_claude_turn_reads_the_result_event(self):
        def fake(cmd, stdin, cwd, timeout, on_line, env, cancel):
            self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
            self.assertEqual(cmd[-2:], ["--allowedTools", "Read"])
            on_line(json.dumps({"type": "result", "result": "answer", "session_id": "c1", "total_cost_usd": 0.3,
                                "permission_denials": [{}], "structured_output": {"a": 1}}) + "\n")
            return 0, ""

        task = agent.AgentTask("q", Path(self.dir.name), claude_allowed_tools=["Read"], run_dir=Path(self.dir.name))
        result = agent.run_claude(task, execute=fake)
        self.assertEqual((result.text, result.session, result.cost_usd, result.refused, result.structured),
                         ("answer", "claude:c1", 0.3, 1, {"a": 1}))

    def test_run_drops_a_session_the_other_engine_owns(self):
        engine.choose("codex")
        calls = []
        with mock.patch.dict(agent.RUNNERS, {"codex": lambda task, cancel: calls.append(task.session) or agent.AgentResult("codex")}):
            agent.run(agent.AgentTask("q", Path("."), session="claude:old"))
        self.assertEqual(calls, [None])

    def test_stream_cancel_kills_the_process(self):
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        with self.assertRaises(InterruptedError):
            agent.stream(["sleep", "10"], "", Path("."), 30, lambda line: None, None, cancel)


if __name__ == "__main__":
    unittest.main()
