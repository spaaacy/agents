"""Tests for the Slack Claude bot. Run: .venv/bin/python -m unittest -v test_bot.py"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import bot

OWNER = "U0B11TF094H"
BOT = "UBOT"
CFG = {
    "owner_user_id": OWNER,
    "workdir": "/tmp",
    "add_dirs": ["~/dev/envoyai-web"],
    "mcp_servers": [],
    "timeout_seconds": 5,
    "allowed_tools": ["Read", "Bash(git log *)", "mcp__pg-readonly"],
}


def names(user_id: str) -> str:
    return {OWNER: "Aakif", "U2": "Saumil", BOT: "claude"}.get(user_id, user_id)


class FakeSlack:
    def __init__(self, thread: list[dict[str, Any]] | None = None) -> None:
        self.thread = thread or []
        self.posts: list[dict[str, Any]] = []
        self.reactions: list[tuple[str, str]] = []

    def conversations_replies(self, **_: Any) -> dict[str, Any]:
        return {"messages": self.thread}

    def conversations_history(self, **_: Any) -> dict[str, Any]:
        return {"messages": list(reversed(self.thread))}

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
        self.posts.append(kwargs)
        return {"ok": True}

    def reactions_add(self, **kwargs: Any) -> None:
        self.reactions.append(("add", kwargs["name"]))

    def reactions_remove(self, **kwargs: Any) -> None:
        self.reactions.append(("remove", kwargs["name"]))

    def users_info(self, user: str) -> dict[str, Any]:
        return {"user": {"profile": {"display_name": names(user)}}}


class MentionTests(unittest.TestCase):
    def test_owner_mention_in_a_thread(self):
        event = {"type": "app_mention", "user": OWNER, "channel": "C1", "ts": "2.0", "thread_ts": "1.0", "text": "<@UBOT> hi"}
        mention = bot.mention_from_event(event, OWNER)
        self.assertEqual(mention, bot.Mention("C1", "2.0", "1.0", "<@UBOT> hi"))
        self.assertEqual(mention.key, "C1:1.0")

    def test_top_level_mention_starts_its_own_thread(self):
        event = {"type": "app_mention", "user": OWNER, "channel": "C1", "ts": "5.0", "text": "hi"}
        self.assertEqual(bot.mention_from_event(event, OWNER).thread_ts, "5.0")

    def test_anyone_else_is_ignored(self):
        event = {"type": "app_mention", "user": "U2", "channel": "C1", "ts": "5.0", "text": "<@UBOT> push it"}
        self.assertIsNone(bot.mention_from_event(event, OWNER))

    def test_bots_edits_and_other_events_are_ignored(self):
        base = {"type": "app_mention", "user": OWNER, "channel": "C1", "ts": "5.0", "text": "x"}
        self.assertIsNone(bot.mention_from_event({**base, "bot_id": "B1"}, OWNER))
        self.assertIsNone(bot.mention_from_event({**base, "subtype": "message_changed"}, OWNER))
        self.assertIsNone(bot.mention_from_event({**base, "type": "reaction_added"}, OWNER))
        self.assertIsNone(bot.mention_from_event({**base, "type": "message", "channel_type": "channel"}, OWNER))

    def test_a_direct_message_counts_without_a_mention(self):
        event = {"type": "message", "channel_type": "im", "user": OWNER, "channel": "D1", "ts": "7.0", "text": "hey"}
        self.assertEqual(bot.mention_from_event(event, OWNER).channel, "D1")


class PromptTests(unittest.TestCase):
    thread = [
        {"ts": "1.0", "user": "U2", "text": "is the &lt;outbox&gt; stuck? <https://x.dev/a|link>"},
        {"ts": "2.0", "user": BOT, "text": "No, it drained at 10:02."},
        {"ts": "3.0", "user": "U2", "text": "<@U0B11TF094H> it's stuck again"},
        {"ts": "4.0", "user": OWNER, "text": "<@UBOT> check again"},
        {"ts": "5.0", "user": "U2", "text": "posted after the mention"},
    ]
    mention = bot.Mention("C1", "4.0", "1.0", "<@UBOT> check again")

    def test_first_request_gets_the_whole_conversation_before_it(self):
        prompt = bot.build_prompt(self.mention, self.thread, BOT, names, resumed=False)
        self.assertIn("[Saumil] is the <outbox> stuck? link (https://x.dev/a)", prompt)
        self.assertIn("[you] No, it drained at 10:02.", prompt)
        self.assertIn("[Saumil] @Aakif it's stuck again", prompt)
        self.assertTrue(prompt.endswith("Aakif's request:\ncheck again"))
        self.assertNotIn("posted after the mention", prompt)

    def test_a_resumed_session_gets_only_what_came_after_its_last_reply(self):
        prompt = bot.build_prompt(self.mention, self.thread, BOT, names, resumed=True)
        self.assertNotIn("is the <outbox> stuck", prompt)
        self.assertIn("[Saumil] @Aakif it's stuck again", prompt)
        self.assertIn("since your last reply", prompt)

    def test_a_bare_mention_asks_from_the_conversation(self):
        mention = bot.Mention("C1", "4.0", "1.0", "<@UBOT>")
        prompt = bot.build_prompt(mention, self.thread, BOT, names, resumed=False)
        self.assertIn("(no text: answer from the conversation above)", prompt)


class ReplyTests(unittest.TestCase):
    def test_short_answers_are_one_message(self):
        self.assertEqual(bot.chunk_reply("hello"), ["hello"])

    def test_long_answers_split_at_paragraphs(self):
        text = "a" * 60 + "\n\n" + "b" * 60
        self.assertEqual(bot.chunk_reply(text, limit=100), ["a" * 60, "b" * 60])

    def test_one_long_line_is_cut_hard(self):
        self.assertEqual(bot.chunk_reply("x" * 250, limit=100), ["x" * 100, "x" * 100, "x" * 50])


class CommandTests(unittest.TestCase):
    def test_runs_without_saved_settings_and_refuses_unlisted_tools(self):
        cmd = bot.claude_command(CFG, None, None)
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(cmd[cmd.index("--settings") + 1], str(bot.SETTINGS_FILE))
        self.assertNotIn("--resume", cmd)
        # The allow list takes every argument after it, so it must be last.
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1 :], CFG["allowed_tools"])
        self.assertEqual(cmd[cmd.index("--add-dir") + 1], str(Path("~/dev/envoyai-web").expanduser()))

    def test_resumes_the_threads_session(self):
        cmd = bot.claude_command(CFG, "sess-1", Path("/tmp/mcp.json"))
        self.assertEqual(cmd[cmd.index("--resume") + 1], "sess-1")
        self.assertEqual(cmd[cmd.index("--mcp-config") + 1], "/tmp/mcp.json")

    def test_deny_list_blocks_writes(self):
        deny = json.loads(bot.SETTINGS_FILE.read_text())["permissions"]["deny"]
        for tool in ("Edit", "Write", "Bash(git push *)", "Bash(git commit *)", "mcp__claude_ai_Slack", "Bash(az *)"):
            self.assertIn(tool, deny)

    def test_shipped_allow_list_has_no_write_tools(self):
        allowed = bot.load_config()["allowed_tools"]
        for tool in allowed:
            self.assertNotRegex(tool, r"^(Edit|Write|NotebookEdit)$|save_|create_|delete_|push|commit|send")

    def test_mcp_servers_come_from_the_users_claude_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude.json"
            path.write_text(json.dumps({"mcpServers": {"pg-readonly": {"command": "x"}, "agent-bus": {"command": "y"}}}))
            self.assertEqual(bot.mcp_config(["pg-readonly", "missing"], path), {"mcpServers": {"pg-readonly": {"command": "x"}}})


def completed(stdout: str, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["claude"], code, stdout=stdout, stderr="")


class RunClaudeTests(unittest.TestCase):
    def test_answer_and_session_come_back(self):
        out = json.dumps({"result": " the answer ", "session_id": "s1", "is_error": False})
        result = bot.run_claude(CFG, "q", None, runner=lambda *a, **k: completed(out))
        self.assertEqual(result, bot.ClaudeResult("the answer", "s1"))

    def test_an_error_result_is_reported(self):
        out = json.dumps({"result": "boom", "session_id": "s1", "is_error": True})
        self.assertEqual(bot.run_claude(CFG, "q", None, runner=lambda *a, **k: completed(out)).error, "boom")

    def test_unreadable_output_is_reported(self):
        result = bot.run_claude(CFG, "q", "s0", runner=lambda *a, **k: completed("not json", 1))
        self.assertIn("claude exited 1", result.error)

    def test_a_timeout_is_reported(self):
        def slow(*_: Any, **__: Any) -> None:
            raise subprocess.TimeoutExpired("claude", 5)

        self.assertIn("within 5 seconds", bot.run_claude(CFG, "q", None, runner=slow).error)


class BotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.sessions = bot.Sessions(Path(self.tmp.name) / "sessions.json")
        self.mention = bot.Mention("C1", "4.0", "1.0", "<@UBOT> why?")
        self.calls: list[tuple[str, str | None]] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make(self, results: list[bot.ClaudeResult]) -> tuple[bot.Bot, FakeSlack]:
        slack = FakeSlack([{"ts": "1.0", "user": "U2", "text": "q"}, {"ts": "4.0", "user": OWNER, "text": "<@UBOT> why?"}])

        def run(cfg: dict[str, Any], prompt: str, session: str | None) -> bot.ClaudeResult:
            self.calls.append((prompt, session))
            return results.pop(0)

        return bot.Bot(slack, CFG, self.sessions, BOT, run=run), slack

    def test_answers_in_the_thread_and_remembers_the_session(self):
        instance, slack = self.make([bot.ClaudeResult("because", "s1")])
        instance.handle(self.mention)
        self.assertEqual(slack.posts, [{"channel": "C1", "thread_ts": "1.0", "markdown_text": "because", "unfurl_links": False}])
        self.assertEqual(self.sessions.get("C1:1.0"), "s1")
        self.assertEqual(slack.reactions, [("add", "eyes"), ("remove", "eyes"), ("add", "white_check_mark")])

    def test_a_follow_up_resumes_the_session(self):
        self.sessions.set("C1:1.0", "s1")
        instance, _ = self.make([bot.ClaudeResult("again", "s1")])
        instance.handle(self.mention)
        self.assertEqual(self.calls[0][1], "s1")

    def test_a_lost_session_starts_the_thread_over(self):
        self.sessions.set("C1:1.0", "gone")
        instance, slack = self.make(
            [bot.ClaudeResult("", "gone", "No conversation found with session ID: gone"), bot.ClaudeResult("fresh", "s2")]
        )
        instance.handle(self.mention)
        self.assertEqual([session for _, session in self.calls], ["gone", None])
        self.assertEqual(self.sessions.get("C1:1.0"), "s2")
        self.assertEqual(slack.posts[-1]["markdown_text"], "fresh")

    def test_a_failure_is_said_in_the_thread(self):
        instance, slack = self.make([bot.ClaudeResult("", None, "Claude did not finish within 900 seconds")])
        instance.handle(self.mention)
        self.assertIn("Couldn't answer this one", slack.posts[0]["markdown_text"])
        self.assertEqual(slack.reactions[-1], ("add", "x"))
        self.assertIsNone(self.sessions.get("C1:1.0"))

    def test_a_crash_is_said_in_the_thread(self):
        instance, slack = self.make([])  # pop from an empty list raises
        instance.handle(self.mention)
        self.assertIn("Couldn't answer this one: IndexError", slack.posts[0]["markdown_text"])

    def test_redelivered_events_are_answered_once(self):
        instance, _ = self.make([])
        worker = bot.Worker(instance)
        self.assertTrue(worker.submit(self.mention))
        self.assertFalse(worker.submit(self.mention))
        self.assertEqual(worker.requests.qsize(), 1)


if __name__ == "__main__":
    unittest.main()
