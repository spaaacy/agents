import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import engine


class Choice(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.file = Path(self.dir.name) / "engine.json"
        patcher = mock.patch.object(engine, "CHOICE_FILE", self.file)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def test_defaults_to_codex_sol_medium(self):
        self.assertEqual(engine.engine(), "codex")
        self.assertEqual(engine.current()["codex"], {"model": "gpt-6.1-sol", "effort": "medium"})

    def test_switch_back_and_forth_keeps_the_codex_settings(self):
        engine.choose("codex", model="gpt-6-sol", effort="high")
        engine.choose("claude")
        self.assertEqual(engine.engine(), "claude")
        engine.choose("codex")
        self.assertEqual(engine.current()["codex"], {"model": "gpt-6-sol", "effort": "high"})

    def test_bad_file_reads_as_the_default(self):
        self.file.write_text("{not json")
        self.assertEqual(engine.engine(), "codex")
        self.file.write_text(json.dumps({"engine": "gemini", "codex": {"effort": "huge"}}))
        self.assertEqual(engine.current()["codex"]["effort"], "medium")

    def test_effort_is_codex_only(self):
        with self.assertRaises(ValueError):
            engine.choose("claude", effort="high")

    def test_claude_model_is_passed_only_when_chosen(self):
        self.assertEqual(engine.claude_model_args(), [])
        engine.choose("claude", model="claude-opus-5-5")
        self.assertEqual(engine.claude_model_args(), ["--model", "claude-opus-5-5"])


class CodexHome(unittest.TestCase):
    def test_home_is_read_only_never_approves_and_loads_only_its_rules(self):
        with tempfile.TemporaryDirectory() as root:
            user = Path(root) / "user"
            user.mkdir()
            (user / "auth.json").write_text("{}")
            (user / "config.toml").write_text(
                '[mcp_servers.linear]\nurl = "https://mcp.linear.app/mcp"\n'
                '[mcp_servers.linear.tools.save_issue]\napproval_mode = "approve"\n'
                '[mcp_servers.pg-readonly]\ncommand = "node"\nargs = ["x.js"]\n'
            )
            with mock.patch.object(engine, "USER_CODEX_HOME", user):
                home = engine.prepare_codex_home(["linear", "pg-readonly", "missing"], home=Path(root) / "home",
                                                 choice=engine.DEFAULT)
            config = tomllib.loads((home / "config.toml").read_text())
            self.assertEqual(config["approval_policy"], "never")
            self.assertEqual(config["model"], "gpt-6.1-sol")
            self.assertEqual(config["permissions"]["background"]["filesystem"][":root"], "read")
            self.assertEqual(config["permissions"]["background"]["filesystem"][str(Path.home() / ".ssh")], "none")
            linear = config["mcp_servers"]["linear"]
            self.assertNotIn("save_issue", linear["enabled_tools"])
            self.assertNotIn("save_issue", linear["tools"])
            self.assertEqual(config["mcp_servers"]["pg-readonly"]["tools"]["run_sql"], {"approval_mode": "approve"})
            self.assertNotIn("missing", config["mcp_servers"])
            self.assertEqual([p.name for p in (home / "rules").iterdir()], ["background.rules"])
            self.assertEqual((home / "sessions").resolve(), (user / "sessions").resolve())
            self.assertEqual(oct((home / "config.toml").stat().st_mode & 0o777), "0o600")

    def test_resume_command_drops_flags_resume_refuses(self):
        cmd = engine.codex_command(Path("/w"), Path("/o"), session="abc")
        self.assertEqual(cmd[1:3], ["exec", "resume"])
        self.assertNotIn("-C", cmd)
        self.assertNotIn("--color", cmd)
        self.assertEqual(cmd[-2:], ["abc", "-"])

    def test_events_give_session_answer_and_tokens(self):
        events = engine.CodexEvents()
        for line in [
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"the answer"}}',
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}',
            "not json",
        ]:
            events.feed(line)
        self.assertEqual((events.session, events.last_message, events.tokens()), ("t1", "the answer", 15))
        self.assertEqual(engine.final_text(Path("/nonexistent"), events), "the answer")


if __name__ == "__main__":
    unittest.main()
