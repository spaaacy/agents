import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from hub.core import engine


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

    def test_claude_takes_model_and_effort_and_rejects_unknown_levels(self):
        engine.choose("claude", model="opus", effort="high")
        self.assertEqual(engine.claude_model_args(), ["--model", "opus", "--effort", "high"])
        with self.assertRaises(ValueError):
            engine.set_effort("ultra")  # a Codex-only level

    def test_model_by_short_name_picks_its_engine(self):
        cache = Path(self.dir.name) / "models_cache.json"
        cache.write_text(json.dumps({"models": [
            {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1-Sol", "visibility": "list", "default_reasoning_level": "low",
             "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high", "ultra")]},
            {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "visibility": "list", "default_reasoning_level": "medium",
             "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high")]},
            {"slug": "hidden", "visibility": "hide"}]}))
        with mock.patch.object(engine, "USER_CODEX_HOME", Path(self.dir.name)):
            self.assertEqual([m.slug for m in engine.models("codex")], ["gpt-6.1-sol", "gpt-6-luna"])
            engine.set_model("opus")
            self.assertEqual((engine.engine(), engine.current()["claude"]["model"]), ("claude", "opus"))
            engine.set_model("sol", effort="ultra")
            self.assertEqual(engine.current()["codex"], {"model": "gpt-6.1-sol", "effort": "ultra"})
            engine.set_model("luna")  # ultra doesn't exist there: back to Luna's own default
            self.assertEqual(engine.current()["codex"], {"model": "gpt-6-luna", "effort": "medium"})
            engine.set_effort("default")
            self.assertEqual(engine.current()["codex"]["effort"], "medium")
            with self.assertRaises(ValueError):
                engine.set_model("nonexistent-model")
            self.assertIn("* gpt-6-luna", engine.listing())


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



class ReviewScope(unittest.TestCase):
    """PR reviews have their own engine/model/effort, switched with the scope word first."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.all_file = Path(self.dir.name) / "engine.json"
        self.review_file = Path(self.dir.name) / "engine-reviews.json"
        for name, value in (("CHOICE_FILE", self.all_file), ("REVIEW_CHOICE_FILE", self.review_file)):
            patcher = mock.patch.object(engine, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_reviews_default_to_astra_xhigh_apart_from_everything_else(self):
        self.assertEqual(engine.current(scope="reviews")["codex"], {"model": "gpt-6-astra", "effort": "xhigh"})
        self.assertEqual(engine.current()["codex"], {"model": "gpt-6.1-sol", "effort": "medium"})

    def test_switching_reviews_leaves_the_other_plugins_alone_and_back(self):
        engine.set_model("sol", "high", scope="reviews")
        self.assertEqual(engine.current(scope="reviews")["codex"], {"model": "gpt-6.1-sol", "effort": "high"})
        self.assertFalse(self.all_file.exists())
        engine.choose("claude", scope="reviews")
        self.assertEqual((engine.engine(scope="reviews"), engine.engine()), ("claude", "codex"))
        engine.set_effort("low")
        self.assertEqual(engine.current()["codex"]["effort"], "low")
        self.assertEqual(engine.current(scope="reviews")["codex"]["effort"], "high")

    def test_the_scope_word_comes_first(self):
        self.assertEqual(engine.split_scope(["reviews", "astra"]), ("reviews", ["astra"]))
        self.assertEqual(engine.split_scope(["review", "xhigh"]), ("reviews", ["xhigh"]))
        self.assertEqual(engine.split_scope(["astra"]), ("all", ["astra"]))
        self.assertEqual(engine.split_scope([]), ("all", []))

    def test_cli_takes_the_scope(self):
        with mock.patch("builtins.print") as printed:
            self.assertEqual(engine.model_main(["reviews", "sol", "--effort", "high"]), 0)
        self.assertIn("PR reviews now run on codex (gpt-6.1-sol, high effort)", printed.call_args[0][0])
        with mock.patch("builtins.print") as printed:
            engine.effort_main(["reviews"])
        self.assertTrue(printed.call_args[0][0].startswith("PR reviews run on"))
        self.assertFalse(self.all_file.exists())


if __name__ == "__main__":
    unittest.main()
