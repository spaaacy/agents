import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.core import sessions


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


class Sessions(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.claude, self.codex = root / "claude", root / "codex"
        for name, value in (("CLAUDE_DIR", self.claude), ("CODEX_DIR", self.codex)):
            patcher = mock.patch.object(sessions, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        write(self.claude / "proj" / "aaaa1111.jsonl", [
            {"type": "user", "entrypoint": "claude-vscode", "cwd": "/w/backend", "gitBranch": "aakif/eng-1",
             "timestamp": "2026-11-12T10:00:00Z", "message": {"content": "<command-message>pickup</command-message>"
             "<command-name>/pickup</command-name><command-args>ENG-1</command-args>"}},
            {"type": "user", "isMeta": True, "timestamp": "2026-11-12T10:00:01Z", "message": {"content": "skill text"}},
            {"type": "assistant", "timestamp": "2026-11-12T10:01:00Z", "message": {"content": [
                {"type": "text", "text": "on it"}, {"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}]}},
            {"type": "user", "timestamp": "2026-11-12T10:01:01Z", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
            {"type": "user", "timestamp": "2026-11-12T10:05:00Z",
             "message": {"content": [{"type": "text", "text": "why is CI red <system-reminder>noise</system-reminder>"}]}},
        ])
        write(self.claude / "proj" / "bbbb2222.jsonl", [
            {"type": "user", "entrypoint": "sdk-cli", "cwd": "/w/backend", "timestamp": "2026-11-12T10:00:00Z",
             "message": {"content": "hub background run"}}])
        write(self.codex / "2026" / "11" / "12" / "rollout-2026-11-12T10-00-00-cccc3333.jsonl", [
            {"type": "session_meta", "timestamp": "2026-11-12T09:00:00Z", "payload": {
                "id": "cccc3333", "cwd": "/w/web", "originator": "codex_vscode", "source": "vscode",
                "git": {"branch": "aakif/eng-2"}, "timestamp": "2026-11-12T09:00:00Z"}},
            {"type": "event_msg", "timestamp": "2026-11-12T09:01:00Z", "payload": {"type": "user_message", "message": "fix the filter"}},
            {"type": "response_item", "timestamp": "2026-11-12T09:02:00Z",
             "payload": {"type": "function_call", "name": "shell", "arguments": json.dumps({"cmd": "npm test"})}},
            {"type": "event_msg", "timestamp": "2026-11-12T09:03:00Z", "payload": {"type": "agent_message", "message": "done, tests pass"}},
        ])
        write(self.codex / "2026" / "11" / "12" / "rollout-2026-11-12T10-00-00-dddd4444.jsonl", [
            {"type": "session_meta", "payload": {"id": "dddd4444", "originator": "codex_vscode",
                                                 "source": {"subagent": {"other": "guardian"}}}}])

    def run_cmd(self, *argv):
        with mock.patch("sys.stdout") as out:
            sessions.main(list(argv))
        return "".join(call.args[0] for call in out.write.call_args_list)

    def test_list_shows_his_sessions_not_the_hubs_and_reads_slash_commands(self):
        out = self.run_cmd("list", "--hours", "1000000")
        self.assertIn("claude aaaa1111", out)
        self.assertIn("~" not in "/w/backend" and "/w/backend @ aakif/eng-1", out)
        self.assertIn("first: /pickup ENG-1", out)
        self.assertIn("latest: why is CI red", out)
        self.assertNotIn("noise", out)
        self.assertIn("codex cccc3333", out)
        self.assertNotIn("bbbb2222", out, "the hub's own claude -p run")
        self.assertNotIn("dddd4444", out, "Codex's automatic-review helper")
        self.assertIn("bbbb2222", self.run_cmd("list", "--hours", "1000000", "--all"))
        self.assertNotIn("aaaa1111", self.run_cmd("list", "--hours", "1000000", "--repo", "web"))

    def test_show_reads_a_transcript(self):
        out = self.run_cmd("show", "aaaa", "--hours", "1000000")
        self.assertIn("[", out)
        self.assertIn("you] /pickup ENG-1", out)
        self.assertIn("claude] on it", out)
        self.assertIn("tool] Bash: git status", out)
        self.assertNotIn("skill text", out)
        codex = self.run_cmd("show", "cccc3333", "--hours", "1000000")
        self.assertIn("you] fix the filter", codex)
        self.assertIn("tool] shell: npm test", codex)
        self.assertIn("codex] done, tests pass", codex)


if __name__ == "__main__":
    unittest.main()
