"""Tests for the Slack Claude bot and its live view. Run: .venv/bin/python -m unittest -v test_bot.py"""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

import bot
import watch

OWNER = "U0B11TF094H"
BOT = "UBOT"
BOT_DM = "DBOT"
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


def route(event: dict[str, Any], *, bot_dm: str | None = BOT_DM, dm_as_user: bool = True) -> bot.Mention | None:
    return bot.mention_from_event(event, OWNER, BOT, bot_dm, dm_as_user)


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
        return {"ok": True, "ts": f"9.{len(self.posts)}"}

    def chat_getPermalink(self, channel: str, message_ts: str) -> dict[str, Any]:
        return {"permalink": f"https://slack.test/{channel}/{message_ts}"}

    def reactions_add(self, **kwargs: Any) -> None:
        self.reactions.append(("add", kwargs["name"]))

    def reactions_remove(self, **kwargs: Any) -> None:
        self.reactions.append(("remove", kwargs["name"]))

    def users_info(self, user: str) -> dict[str, Any]:
        return {"user": {"profile": {"display_name": names(user)}}}


class RoutingTests(unittest.TestCase):
    def test_a_mention_in_a_thread_is_answered_by_the_bot(self):
        event = {"type": "app_mention", "user": OWNER, "channel": "C1", "ts": "2.0", "thread_ts": "1.0", "text": "<@UBOT> hi"}
        self.assertEqual(route(event), bot.Mention("C1", "2.0", "1.0", "<@UBOT> hi"))
        self.assertEqual(route(event).key, "C1:1.0")

    def test_a_top_level_mention_starts_its_own_thread(self):
        event = {"type": "app_mention", "user": OWNER, "channel": "C1", "ts": "5.0", "text": "hi"}
        self.assertEqual(route(event).thread_ts, "5.0")

    def test_anyone_else_is_ignored_everywhere(self):
        for event in (
            {"type": "app_mention", "user": "U2", "channel": "C1", "ts": "5.0", "text": "<@UBOT> push it"},
            {"type": "message", "channel_type": "im", "user": "U2", "channel": "D2", "ts": "5.0", "text": "@claude push"},
        ):
            self.assertIsNone(route(event))

    def test_bots_edits_and_other_events_are_ignored(self):
        base = {"type": "app_mention", "user": OWNER, "channel": "C1", "ts": "5.0", "text": "x"}
        self.assertIsNone(route({**base, "bot_id": "B1"}))
        self.assertIsNone(route({**base, "subtype": "message_changed"}))
        self.assertIsNone(route({**base, "type": "reaction_added"}))
        self.assertIsNone(route({**base, "type": "message", "channel_type": "channel"}))

    def test_everything_in_the_bots_own_dm_is_for_the_bot(self):
        event = {"type": "message", "channel_type": "im", "user": OWNER, "channel": BOT_DM, "ts": "7.0", "text": "hey"}
        mention = route(event)
        self.assertEqual((mention.channel, mention.as_user), (BOT_DM, False))

    def test_a_dm_with_a_teammate_needs_the_trigger_and_is_answered_as_the_owner(self):
        event = {"type": "message", "channel_type": "im", "user": OWNER, "channel": "D2", "ts": "7.0", "text": "@claude is the outbox stuck?"}
        mention = route(event)
        self.assertEqual((mention.text, mention.as_user), ("is the outbox stuck?", True))
        self.assertIsNone(route({**event, "text": "lunch?"}))

    def test_other_dms_stay_off_without_the_owners_token(self):
        event = {"type": "message", "channel_type": "im", "user": OWNER, "channel": "D2", "ts": "7.0", "text": "@claude hi"}
        self.assertIsNone(route(event, dm_as_user=False))

    def test_with_the_bots_dm_unknown_no_dm_is_answered_as_the_bot(self):
        # 2026-11-06: the app was reinstalled under a running bot that had no im:write;
        # a DM with a teammate was taken for the bot's own and the reply failed.
        event = {"type": "message", "channel_type": "im", "user": OWNER, "channel": "D9", "ts": "7.0", "text": "<@UBOT>"}
        self.assertIsNone(route(event, bot_dm=None, dm_as_user=False))

    def test_a_real_mention_in_a_group_dm_is_left_to_the_bot(self):
        event = {"type": "message", "channel_type": "mpim", "user": OWNER, "channel": "G1", "ts": "7.0", "text": "<@UBOT> hi"}
        self.assertIsNone(route(event))

    def test_its_own_marked_answers_never_trigger_it(self):
        event = {"type": "message", "channel_type": "im", "user": OWNER, "channel": "D2", "ts": "8.0", "text": "🤖 claude: @claude said so"}
        self.assertIsNone(route(event))

    def test_trigger_forms(self):
        self.assertEqual(bot.strip_trigger("@claude what", BOT), "what")
        self.assertEqual(bot.strip_trigger("  @Claude-aakif: what", BOT), "what")
        self.assertEqual(bot.strip_trigger("<@UBOT|claude-aakif> what", BOT), "what")
        self.assertIsNone(bot.strip_trigger("claude what", BOT))
        self.assertIsNone(bot.strip_trigger("ask @claude what", BOT))
        self.assertIsNone(bot.strip_trigger("@claudette hi", BOT))


class PromptTests(unittest.TestCase):
    thread = [
        {"ts": "1.0", "user": "U2", "text": "is the &lt;outbox&gt; stuck? <https://x.dev/a|link>"},
        {"ts": "2.0", "user": BOT, "text": "No, it drained at 10:02."},
        {"ts": "3.0", "user": "U2", "text": "<@U0B11TF094H> it's stuck again"},
        {"ts": "4.0", "user": OWNER, "text": "<@UBOT> check again"},
        {"ts": "5.0", "user": "U2", "text": "posted after the request"},
    ]
    mention = bot.Mention("C1", "4.0", "1.0", "<@UBOT> check again")

    def test_first_request_gets_the_whole_conversation_before_it(self):
        prompt = bot.build_prompt(self.mention, self.thread, BOT, names, resumed=False)
        self.assertIn("[Saumil] is the <outbox> stuck? link (https://x.dev/a)", prompt)
        self.assertIn("[you] No, it drained at 10:02.", prompt)
        self.assertIn("[Saumil] @Aakif it's stuck again", prompt)
        self.assertTrue(prompt.endswith("Aakif's request:\ncheck again"))
        self.assertNotIn("posted after the request", prompt)

    def test_a_resumed_session_gets_only_what_came_after_its_last_answer(self):
        prompt = bot.build_prompt(self.mention, self.thread, BOT, names, resumed=True)
        self.assertNotIn("is the <outbox> stuck", prompt)
        self.assertIn("[Saumil] @Aakif it's stuck again", prompt)
        self.assertIn("since your last answer", prompt)

    def test_answers_posted_as_the_owner_count_as_its_own(self):
        thread = [
            {"ts": "1.0", "user": OWNER, "text": "@claude why?"},
            {"ts": "2.0", "user": OWNER, "text": "🤖 claude: because"},
            {"ts": "3.0", "user": "U2", "text": "and then?"},
        ]
        mention = bot.Mention("D2", "4.0", "1.0", "and then?", as_user=True)
        is_reply = bot.reply_detector(mention, BOT, OWNER)
        prompt = bot.build_prompt(mention, thread, BOT, names, resumed=True, is_reply=is_reply)
        self.assertNotIn("why?", prompt)
        self.assertIn("[Saumil] and then?", prompt)
        full = bot.build_prompt(mention, thread, BOT, names, resumed=False, is_reply=is_reply)
        self.assertIn("[you] 🤖 claude: because", full)

    def test_a_bare_mention_asks_from_the_conversation(self):
        mention = bot.Mention("C1", "4.0", "1.0", "<@UBOT>")
        self.assertIn("(he only mentioned you: answer the open question above", bot.build_prompt(mention, self.thread, BOT, names, resumed=False))


class ReplyTests(unittest.TestCase):
    def test_short_answers_are_one_message(self):
        self.assertEqual(bot.chunk_reply("hello"), ["hello"])

    def test_long_answers_split_at_paragraphs(self):
        self.assertEqual(bot.chunk_reply("a" * 60 + "\n\n" + "b" * 60, limit=100), ["a" * 60, "b" * 60])

    def test_one_long_line_is_cut_hard(self):
        self.assertEqual(bot.chunk_reply("x" * 250, limit=100), ["x" * 100, "x" * 100, "x" * 50])


class CommandTests(unittest.TestCase):
    def test_runs_without_saved_settings_and_refuses_unlisted_tools(self):
        cmd = bot.claude_command(CFG, None, None)
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", cmd)  # stream-json requires it
        self.assertEqual(cmd[cmd.index("--settings") + 1], str(bot.SETTINGS_FILE))
        self.assertNotIn("--resume", cmd)
        # The allow list takes every argument after it, so it must be last.
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1 :], CFG["allowed_tools"])

    def test_resumes_the_threads_session(self):
        cmd = bot.claude_command(CFG, "sess-1", Path("/tmp/mcp.json"))
        self.assertEqual(cmd[cmd.index("--resume") + 1], "sess-1")
        self.assertEqual(cmd[cmd.index("--mcp-config") + 1], "/tmp/mcp.json")

    def test_deny_list_blocks_writes(self):
        deny = json.loads(bot.SETTINGS_FILE.read_text())["permissions"]["deny"]
        for tool in ("Edit", "Write", "Bash(git push *)", "Bash(git -C * push *)", "Bash(git commit *)", "mcp__claude_ai_Slack", "Bash(az *)"):
            self.assertIn(tool, deny)

    def test_shipped_allow_list_has_no_write_tools(self):
        for tool in bot.load_config()["allowed_tools"]:
            self.assertNotRegex(tool, r"^(Edit|Write|NotebookEdit)$|save_|create_|delete_|push|commit|send")

    def test_the_voice_rules_come_live_from_agents_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AGENTS.md"
            path.write_text("# A\n\n## Communication style\n\nshort.\n\n## Voice\n\nlowercase.\n\n## Tools\n\nx\n")
            rules = bot.voice_rules(path)
            self.assertIn("Communication style:\nshort.", rules)
            self.assertIn("Voice:\nlowercase.", rules)
            self.assertNotIn("Tools", rules)
            self.assertEqual(bot.voice_rules(Path(tmp) / "missing.md"), "")

    def test_the_prompt_carries_the_voice_rules(self):
        prompt = bot.claude_command(CFG, None, None)
        appended = prompt[prompt.index("--append-system-prompt") + 1]
        self.assertTrue(appended.startswith(bot.SYSTEM_PROMPT))
        self.assertIn("Voice:\n", appended)

    def test_mcp_servers_come_from_the_users_claude_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude.json"
            path.write_text(json.dumps({"mcpServers": {"pg-readonly": {"command": "x"}, "agent-bus": {"command": "y"}}}))
            self.assertEqual(bot.mcp_config(["pg-readonly", "missing"], path), {"mcpServers": {"pg-readonly": {"command": "x"}}})


def executes(lines: list[str], code: int = 0, stderr: str = "") -> bot.Execute:
    def execute(cmd: list[str], prompt: str, cwd: Path, timeout: int, on_line: bot.LineHandler) -> tuple[int, str]:
        for line in lines:
            on_line(line)
        return code, stderr

    return execute


RESULT = json.dumps({"type": "result", "result": " the answer ", "session_id": "s1", "is_error": False, "total_cost_usd": 0.21, "permission_denials": [{}]})


class RunClaudeTests(unittest.TestCase):
    def test_the_answer_comes_from_the_result_event_and_every_event_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = Path(tmp) / "events.jsonl"
            lines = ['{"type":"system","subtype":"init","session_id":"s1"}\n', "not json\n", RESULT]
            result = bot.run_claude(CFG, "q", None, events, execute=executes(lines))
            self.assertEqual(result, bot.ClaudeResult("the answer", "s1", None, 0.21, 1))
            self.assertEqual(len(events.read_text().splitlines()), 3)

    def test_an_error_result_is_reported(self):
        line = json.dumps({"type": "result", "result": "boom", "session_id": "s1", "is_error": True})
        self.assertEqual(bot.run_claude(CFG, "q", None, execute=executes([line])).error, "boom")

    def test_no_result_reports_the_exit_and_stderr(self):
        result = bot.run_claude(CFG, "q", "s0", execute=executes([], 1, "line1\nNo conversation found with session ID: s0\n"))
        self.assertIn("claude exited 1", result.error)
        self.assertIn("No conversation found", result.error)

    def test_a_timeout_is_reported(self):
        def slow(*_: Any) -> tuple[int, str]:
            raise subprocess.TimeoutExpired("claude", 5)

        self.assertIn("within 5 seconds", bot.run_claude(CFG, "q", None, execute=slow).error)


class StreamProcessTests(unittest.TestCase):
    def test_lines_arrive_as_they_are_printed(self):
        seen: list[str] = []
        code, _ = bot.stream_process([sys.executable, "-c", "import sys; print(sys.stdin.read().upper()); print('b')"], "a", Path("/tmp"), 10, seen.append)
        self.assertEqual((code, [line.strip() for line in seen]), (0, ["A", "b"]))

    def test_a_run_past_the_timeout_is_killed(self):
        start = time.time()
        with self.assertRaises(subprocess.TimeoutExpired):
            bot.stream_process([sys.executable, "-c", "import time; time.sleep(30)"], "", Path("/tmp"), 1, lambda _: None)
        self.assertLess(time.time() - start, 10)


class BotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sessions = bot.Sessions(self.root / "sessions.json")
        self.mention = bot.Mention("C1", "4.0", "1.0", "<@UBOT> why?")
        self.calls: list[tuple[str, str | None, Path | None]] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make(self, results: list[bot.ClaudeResult]) -> tuple[bot.Bot, FakeSlack, FakeSlack]:
        thread = [{"ts": "1.0", "user": "U2", "text": "q"}, {"ts": "4.0", "user": OWNER, "text": "<@UBOT> why?"}]
        slack, user_slack = FakeSlack(thread), FakeSlack(thread)

        def run(cfg: dict[str, Any], prompt: str, session: str | None, events: Path | None = None) -> bot.ClaudeResult:
            self.calls.append((prompt, session, events))
            return results.pop(0)

        return bot.Bot(slack, CFG, self.sessions, BOT, run=run, user_web=user_slack, bot_dm=BOT_DM), slack, user_slack

    def test_answers_in_the_thread_and_remembers_the_session(self):
        instance, slack, user_slack = self.make([bot.ClaudeResult("because", "s1")])
        instance.handle(self.mention)
        self.assertEqual(slack.posts, [{"channel": "C1", "thread_ts": "1.0", "markdown_text": "because", "unfurl_links": False}])
        self.assertEqual(self.sessions.get("C1:1.0"), "s1")
        self.assertEqual(slack.reactions, [("add", "eyes"), ("remove", "eyes"), ("add", "white_check_mark")])
        self.assertEqual(user_slack.posts, [])

    def test_a_dm_request_is_answered_under_the_owners_name_and_marked(self):
        instance, slack, user_slack = self.make([bot.ClaudeResult("because", "s1")])
        instance.handle(bot.Mention("D2", "4.0", "1.0", "why?", as_user=True))
        self.assertEqual(user_slack.posts[0]["markdown_text"], "🤖 claude: because")
        self.assertEqual(user_slack.reactions[-1], ("add", "white_check_mark"))
        self.assertEqual((slack.posts, slack.reactions), ([], []))

    def test_a_follow_up_resumes_the_session(self):
        self.sessions.set("C1:1.0", "s1")
        instance, _, _ = self.make([bot.ClaudeResult("again", "s1")])
        instance.handle(self.mention)
        self.assertEqual(self.calls[0][1], "s1")

    def test_a_lost_session_starts_the_thread_over(self):
        self.sessions.set("C1:1.0", "gone")
        instance, slack, _ = self.make([bot.ClaudeResult("", "gone", "No conversation found with session ID: gone"), bot.ClaudeResult("fresh", "s2")])
        instance.handle(self.mention)
        self.assertEqual([call[1] for call in self.calls], ["gone", None])
        self.assertEqual(self.sessions.get("C1:1.0"), "s2")
        self.assertEqual(slack.posts[-1]["markdown_text"], "fresh")

    def test_a_failure_is_said_in_the_thread(self):
        instance, slack, _ = self.make([bot.ClaudeResult("", None, "Claude did not finish within 900 seconds")])
        instance.handle(self.mention)
        self.assertIn("Couldn't answer this one", slack.posts[0]["markdown_text"])
        self.assertEqual(slack.reactions[-1], ("add", "x"))
        self.assertIsNone(self.sessions.get("C1:1.0"))

    def test_a_crash_is_said_in_the_thread(self):
        instance, slack, _ = self.make([])  # pop from an empty list raises
        instance.handle(self.mention)
        self.assertIn("Couldn't answer this one: IndexError", slack.posts[0]["markdown_text"])

    def test_the_request_record_follows_it_to_the_end(self):
        instance, _, _ = self.make([bot.ClaudeResult("because", "s1", None, 0.3, 2)])
        log = bot.RequestLog(self.root / "requests")
        record = log.open(self.mention)
        self.assertEqual(json.loads((record / "request.json").read_text())["status"], "queued")
        instance.handle(self.mention, record)
        data = json.loads((record / "request.json").read_text())
        self.assertEqual(data["status"], "answered")
        self.assertEqual(data["reply_url"], "https://slack.test/C1/9.1")
        self.assertEqual((data["session_id"], data["cost_usd"], data["refused"]), ("s1", 0.3, 2))
        self.assertEqual(self.calls[0][2], record / "events.jsonl")

    def test_redelivered_events_are_answered_once(self):
        instance, _, _ = self.make([])
        worker = bot.Worker(instance)
        self.assertTrue(worker.submit(self.mention))
        self.assertFalse(worker.submit(self.mention))
        self.assertEqual(worker.requests.qsize(), 1)


class WatchTests(unittest.TestCase):
    def test_claude_events_read_as_progress(self):
        event = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Checking the outbox."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "git -C ~/dev/envoyai-backend log -1"}},
            {"type": "tool_use", "name": "mcp__pg-readonly__run_sql", "input": {"sql": "select 1"}},
        ]}}
        self.assertEqual(watch.render(event)[:2], ["Claude: Checking the outbox.", "Running: git -C ~/dev/envoyai-backend log -1"])
        self.assertTrue(watch.render(event)[2].startswith("Calling: pg-readonly.run_sql"))

    def test_refused_tools_and_the_result_are_shown(self):
        refused = {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True, "content": "Permission denied"}]}}
        self.assertEqual(watch.render(refused), ["Tool refused or failed: Permission denied"])
        done = {"type": "result", "duration_ms": 41000, "total_cost_usd": 0.25, "permission_denials": [{}]}
        self.assertEqual(watch.render(done), ["Claude done in 41s, $0.25, 1 tool calls refused"])

    def test_health_line(self):
        now = time.time()
        self.assertIn("not started", watch.health_line(None, now))
        self.assertIn("looks stopped", watch.health_line({"heartbeat_at": now - 120}, now))
        line = watch.health_line({"heartbeat_at": now, "connected": True, "queued": 1, "current": "abc", "dm_as_user": True}, now)
        self.assertEqual(line, "@claude-aakif — connected to Slack; answering abc; 1 queued; answers in other DMs on.")

    def test_terminal_control_sequences_are_stripped(self):
        self.assertEqual(watch.clean("a\x1b[31mred\x1b[0m\x07b"), "aredb")


if __name__ == "__main__":
    unittest.main()
