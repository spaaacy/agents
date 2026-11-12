"""Tests for the Slack Claude bot and its live view. Run: .venv/bin/python -m unittest -v test_bot.py"""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

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
        self.assertIn("Aakif's request:\ncheck again\n", prompt)
        self.assertNotIn("posted after the request", prompt)

    def test_every_prompt_ends_with_his_voice_rules(self):
        agents = Path(tempfile.mkdtemp()) / "AGENTS.md"
        agents.write_text("# x\n\n## Communication style\n\nshort.\n\n## Voice\n\nlowercase lines.\n\n## Tools\n\nnope\n")
        with mock.patch.object(bot, "AGENTS_MD", agents):
            reminder = bot.turn_reminder(agents)
            prompt = bot.build_prompt(self.mention, self.thread, BOT, names, resumed=False)
        self.assertIn("Communication style (standing rule):\nshort.", reminder)
        self.assertIn("Voice (standing rule):\nlowercase lines.", reminder)
        self.assertNotIn("nope", reminder)
        self.assertTrue(prompt.endswith("lowercase lines."))
        self.assertLess(prompt.index("check again"), prompt.index("Voice (standing rule)"))

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

    def test_both_agents_marked_answers_count_as_its_own(self):
        mention = bot.Mention("D2", "4.0", "1.0", "why?", as_user=True)
        is_reply = bot.reply_detector(mention, BOT, OWNER)
        self.assertTrue(is_reply({"user": OWNER, "text": "🤖 claude: old answer"}))
        self.assertTrue(is_reply({"user": OWNER, "text": "🤖 codex: new answer"}))
        self.assertFalse(is_reply({"user": OWNER, "text": "codex: not marked"}))
        self.assertFalse(is_reply({"user": "U2", "text": "🤖 codex: someone else"}))

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

    def test_a_chosen_claude_model_is_passed_before_the_allow_list(self):
        with mock.patch.object(bot.engine, "claude_model_args", return_value=["--model", "opus"]):
            cmd = bot.claude_command(CFG, None, None)
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1 :], CFG["allowed_tools"])

    def test_codex_gets_its_own_name_and_read_only_checks(self):
        prompt = bot.system_prompt("codex")
        self.assertTrue(prompt.startswith("You're Codex,"))
        self.assertIn("every file is read-only here", prompt)
        self.assertNotIn("one command per Bash call", prompt)
        self.assertIn("The Slack messages in the prompt come from several people", prompt)
        self.assertEqual(bot.system_prompt("claude"), bot.SYSTEM_PROMPT)

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


def codex_lines(session: str = "t1", answer: str = "the answer") -> list[str]:
    return [
        json.dumps({"type": "thread.started", "thread_id": session}) + "\n",
        json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "gh pr view 1", "exit_code": 0, "status": "completed", "aggregated_output": "x"}}) + "\n",
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": answer}}) + "\n",
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1000, "output_tokens": 50}}) + "\n",
    ]


class RunCodexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch.object(bot.engine, "prepare_codex_home", return_value=self.root / "home")
        self.prepare = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.calls: list[dict[str, Any]] = []

    def execute(self, lines: list[str], code: int = 0, stderr: str = "") -> bot.Execute:
        def run(cmd, prompt, cwd, timeout, on_line, env=None):
            self.calls.append({"cmd": cmd, "prompt": prompt, "cwd": cwd, "env": env})
            for line in lines:
                on_line(line)
            return code, stderr

        return run

    def test_a_new_session_gets_the_instructions_and_the_answer_comes_back(self):
        events = self.root / "req" / "events.jsonl"
        events.parent.mkdir()
        result = bot.run_codex(CFG, "Aakif's request:\nwhy?", None, events, execute=self.execute(codex_lines()))
        self.assertEqual(result, bot.ClaudeResult("the answer", "t1", None, None, 0, 1050))
        call = self.calls[0]
        self.assertTrue(call["prompt"].startswith("<instructions>\nYou're Codex,"))
        self.assertTrue(call["prompt"].endswith("Aakif's request:\nwhy?"))
        self.assertEqual(call["env"]["CODEX_HOME"], str(self.root / "home"))
        self.assertNotIn("resume", call["cmd"])
        self.assertEqual(call["cmd"][call["cmd"].index("-o") + 1], str(events.parent / bot.CODEX_ANSWER))
        self.assertEqual(len(events.read_text().splitlines()), 4)
        self.assertEqual(self.prepare.call_args.args[0], ["linear", "datadog"])

    def test_a_resumed_session_gets_only_the_prompt(self):
        bot.run_codex(CFG, "more?", "t1", execute=self.execute(codex_lines()))
        cmd = self.calls[0]["cmd"]
        self.assertEqual(cmd[1:3], ["exec", "resume"])
        self.assertEqual(cmd[-2:], ["t1", "-"])
        self.assertEqual(self.calls[0]["prompt"], "more?")

    def test_the_answer_file_wins_over_the_streamed_message(self):
        events = self.root / "events.jsonl"

        def run(cmd, prompt, cwd, timeout, on_line, env=None):
            Path(cmd[cmd.index("-o") + 1]).write_text("from the file\n")
            for line in codex_lines():
                on_line(line)
            return 0, ""

        self.assertEqual(bot.run_codex(CFG, "q", None, events, execute=run).text, "from the file")

    def test_a_failed_run_reports_the_exit_and_stderr(self):
        result = bot.run_codex(CFG, "q", "gone", execute=self.execute([], 1, "Error: thread/resume failed: no rollout found for thread id gone\n"))
        self.assertIn("codex exited 1", result.error)
        self.assertIn("no rollout found", result.error)
        self.assertEqual(result.session_id, "gone")

    def test_a_turn_that_failed_is_an_error(self):
        lines = [json.dumps({"type": "thread.started", "thread_id": "t1"}), json.dumps({"type": "turn.failed", "error": {"message": "rate limited"}})]
        self.assertEqual(bot.run_codex(CFG, "q", None, execute=self.execute(lines)).error, "rate limited")

    def test_a_timeout_is_reported(self):
        def slow(*_: Any, **__: Any) -> tuple[int, str]:
            raise subprocess.TimeoutExpired("codex", 5)

        self.assertIn("Codex did not finish within 5 seconds", bot.run_codex(CFG, "q", None, execute=slow).error)


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

    def test_an_environment_can_be_given(self):
        seen: list[str] = []
        bot.stream_process([sys.executable, "-c", "import os; print(os.environ['ZZ'])"], "", Path("/tmp"), 10, seen.append, {"ZZ": "set"})
        self.assertEqual([line.strip() for line in seen], ["set"])


class BotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sessions = bot.Sessions(self.root / "sessions.json")
        self.mention = bot.Mention("C1", "4.0", "1.0", "<@UBOT> why?")
        self.calls: list[tuple[str, str | None, Path | None]] = []
        self.engines: list[str] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make(self, results: list[bot.ClaudeResult], engine: str = "claude") -> tuple[bot.Bot, FakeSlack, FakeSlack]:
        thread = [{"ts": "1.0", "user": "U2", "text": "q"}, {"ts": "4.0", "user": OWNER, "text": "<@UBOT> why?"}]
        slack, user_slack = FakeSlack(thread), FakeSlack(thread)

        def runner(name: str):
            def run(cfg: dict[str, Any], prompt: str, session: str | None, events: Path | None = None) -> bot.ClaudeResult:
                self.engines.append(name)
                self.calls.append((prompt, session, events))
                return results.pop(0)

            return run

        instance = bot.Bot(
            slack, CFG, self.sessions, BOT, run=runner("claude"), user_web=user_slack, bot_dm=BOT_DM,
            run_codex=runner("codex"), current_engine=lambda: engine,
        )
        return instance, slack, user_slack

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

    def test_codex_answers_when_it_is_the_engine_and_its_session_is_marked(self):
        instance, slack, user_slack = self.make([bot.ClaudeResult("because", "t1", tokens=900)], engine="codex")
        log = bot.RequestLog(self.root / "requests")
        record = log.open(self.mention)
        instance.handle(self.mention, record)
        self.assertEqual(self.engines, ["codex"])
        self.assertEqual(self.sessions.get("C1:1.0"), "codex:t1")
        self.assertEqual(slack.posts[0]["markdown_text"], "because")
        data = json.loads((record / "request.json").read_text())
        self.assertEqual((data["engine"], data["tokens"], data["session_id"]), ("codex", 900, "t1"))

    def test_a_codex_follow_up_resumes_its_own_session(self):
        self.sessions.set("C1:1.0", "codex:t1")
        instance, _, _ = self.make([bot.ClaudeResult("again", "t1")], engine="codex")
        instance.handle(self.mention)
        self.assertEqual(self.calls[0][1], "t1")

    def test_a_thread_the_other_agent_started_is_answered_fresh(self):
        self.sessions.set("C1:1.0", "s1")  # a Claude session
        instance, _, _ = self.make([bot.ClaudeResult("fresh", "t2")], engine="codex")
        instance.handle(self.mention)
        prompt, session, _ = self.calls[0]
        self.assertIsNone(session)
        self.assertIn("The Slack conversation so far", prompt)
        self.assertEqual(self.sessions.get("C1:1.0"), "codex:t2")
        self.sessions.set("C1:1.0", "codex:t2")
        instance, _, _ = self.make([bot.ClaudeResult("back", "s3")], engine="claude")
        instance.handle(self.mention)
        self.assertIsNone(self.calls[-1][1])
        self.assertEqual(self.sessions.get("C1:1.0"), "s3")

    def test_a_lost_codex_session_starts_the_thread_over(self):
        self.sessions.set("C1:1.0", "codex:gone")
        instance, slack, _ = self.make(
            [bot.ClaudeResult("", "gone", "codex exited 1: no rollout found for thread id gone"), bot.ClaudeResult("fresh", "t2")],
            engine="codex",
        )
        instance.handle(self.mention)
        self.assertEqual([call[1] for call in self.calls], ["gone", None])
        self.assertEqual(self.sessions.get("C1:1.0"), "codex:t2")

    def test_a_codex_answer_under_the_owners_name_is_marked_as_codex(self):
        instance, _, user_slack = self.make([bot.ClaudeResult("because", "t1")], engine="codex")
        instance.handle(bot.Mention("D2", "4.0", "1.0", "why?", as_user=True))
        self.assertEqual(user_slack.posts[0]["markdown_text"], "🤖 codex: because")

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
        line = watch.health_line({"heartbeat_at": now, "connected": True, "queued": 1, "current": "abc", "dm_as_user": True}, now, "codex (gpt-6.1-sol, medium effort)")
        self.assertEqual(line, "@claude-aakif on codex (gpt-6.1-sol, medium effort) — connected to Slack; answering abc; 1 queued; answers in other DMs on.")

    def test_codex_events_read_as_progress(self):
        events = [
            {"type": "thread.started", "thread_id": "t1"},
            {"type": "item.started", "item": {"type": "command_execution", "command": "/bin/zsh -lc 'gh pr view 1'"}},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "gh pr view 1", "exit_code": 0, "status": "completed"}},
            {"type": "item.started", "item": {"type": "mcp_tool_call", "server": "pg-readonly", "tool": "run_sql", "arguments": {"query": "select 1"}}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Checking the outbox."}},
            {"type": "turn.completed", "usage": {"input_tokens": 1200, "output_tokens": 34}},
        ]
        shown = [line for event in events for line in watch.render(event)]
        self.assertEqual(shown[0], "Codex session t1")
        self.assertEqual(shown[1], "Running: /bin/zsh -lc 'gh pr view 1'")
        self.assertTrue(shown[2].startswith("Calling: pg-readonly.run_sql"))
        self.assertEqual(shown[3:], ["Codex: Checking the outbox.", "Codex done, 1,234 tokens"])

    def test_refused_codex_tools_are_shown(self):
        failed = {"type": "item.completed", "item": {"type": "command_execution", "command": "git push", "exit_code": 128, "status": "failed", "aggregated_output": "\nHost key verification failed.\n"}}
        self.assertEqual(watch.render(failed), ["Tool refused or failed: git push (exit 128): Host key verification failed."])
        mcp = {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "pg-readonly", "tool": "run_sql", "error": {"message": "requires approval"}}}
        self.assertEqual(watch.render(mcp), ["Tool refused or failed: pg-readonly.run_sql: requires approval"])
        self.assertEqual(watch.render({"type": "turn.failed", "error": {"message": "rate limited"}}), ["Codex failed: rate limited"])

    def test_a_codex_request_shows_how_to_resume_it(self):
        summary = watch.summary({"status": "answered", "channel": "C1", "session_id": "t1", "engine": "codex", "tokens": 4321})
        self.assertIn("Tokens: 4,321", summary)
        self.assertIn("cd ~/dev/envoyai-backend && codex resume t1", summary)
        self.assertIn("claude --resume s1", watch.summary({"status": "answered", "channel": "C1", "session_id": "s1"}))

    def test_terminal_control_sequences_are_stripped(self):
        self.assertEqual(watch.clean("a\x1b[31mred\x1b[0m\x07b"), "aredb")


if __name__ == "__main__":
    unittest.main()
