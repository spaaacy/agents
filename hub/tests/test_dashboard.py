import json
import socket
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.request
from pathlib import Path

from hub.core import events
from hub.core.dashboard import serve_dashboard
from hub.core.hub import Hub

CODEX = [
    {"type": "thread.started", "thread_id": "t-1"},
    {"type": "item.started", "item": {"type": "command_execution", "command": "/bin/zsh -lc 'gh pr view 1'"}},
    {"type": "item.completed", "item": {"type": "command_execution", "command": "gh api user", "exit_code": None,
                                        "status": "failed", "aggregated_output": "rejected: read-only run"}},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "the answer"}},
    {"type": "turn.completed", "usage": {"input_tokens": 1000, "output_tokens": 234}},
]
CLAUDE = [
    {"type": "system", "subtype": "init", "session_id": "c-1"},
    {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "secret"},
                                                  {"type": "tool_use", "name": "Bash", "input": {"command": "git log -1"}},
                                                  {"type": "text", "text": "done \x1b[31mred"}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True, "content": "refused"}]}},
    {"type": "result", "duration_ms": 4200, "total_cost_usd": 0.12, "permission_denials": [{}]},
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Render(unittest.TestCase):
    def test_codex_events(self):
        lines = [line for event in CODEX for line in events.render(event)]
        self.assertEqual(lines[0], "Codex session t-1")
        self.assertIn("Running: /bin/zsh -lc 'gh pr view 1'", lines)
        self.assertTrue(any(line.startswith("Tool refused or failed: gh api user") for line in lines))
        self.assertIn("Codex: the answer", lines)
        self.assertEqual(lines[-1], "Codex done, 1,234 tokens")

    def test_claude_events_leave_thinking_out(self):
        lines = [line for event in CLAUDE for line in events.render(event)]
        self.assertEqual(lines[0], "Claude session c-1")
        self.assertIn("Running: git log -1", lines)
        self.assertNotIn("secret", " ".join(lines))
        self.assertIn("Tool refused or failed: refused", lines)
        self.assertEqual(lines[-1], "Claude done in 4s, $0.12, 1 tool calls refused")

    def test_render_line_strips_terminal_codes_and_skips_junk(self):
        self.assertEqual(events.render_line(json.dumps(CLAUDE[1]))[-1], "Claude: done red")
        self.assertEqual(events.render_line("not json"), [])
        self.assertEqual(events.render("not a dict"), [])

    def test_read_events_tails_from_an_offset_and_waits_for_whole_lines(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "events.jsonl"
            path.write_text(json.dumps(CODEX[0]) + "\n" + json.dumps(CODEX[3])[:10])
            lines, offset = events.read_events(path, 0)
            self.assertEqual(lines, ["Codex session t-1"])
            with path.open("a") as f:
                f.write(json.dumps(CODEX[3])[10:] + "\n")
            lines, offset2 = events.read_events(path, offset)
            self.assertEqual(lines, ["Codex: the answer"])
            self.assertEqual(events.read_events(path, offset2), ([], offset2))
            self.assertEqual(events.read_events(Path(root) / "missing", 5), ([], 5))


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.port = free_port()
        cfg = {"owner": {"slack_user_id": "U1"}, "plugins": {}, "dashboard": {"host": "127.0.0.1", "port": self.port}}
        self.hub = Hub(cfg, state_dir=Path(self.tmp.name), with_slack=False)
        self.server = serve_dashboard(self.hub)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.port}"
        self.token = (Path(self.tmp.name) / "dashboard.token").read_text().strip()

    def request(self, path, *, data=None, headers=None, method=None):
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def post(self, words, headers=None):
        body = json.dumps({"words": words}).encode()
        return self.request("/api/command", data=body, method="POST",
                            headers={"Content-Type": "application/json", **(headers or {})})

    def test_token_file_is_private(self):
        mode = (Path(self.tmp.name) / "dashboard.token").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_page_and_state(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Agents hub", body)
        status, body = self.request("/api/state")
        state = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("engine_text", state)
        self.assertEqual(state["jobs"], [])

    def test_tab_icon_and_the_on_call_notebook(self):
        status, body = self.request("/favicon.png")
        self.assertEqual((status, body[:4]), (200, b"\x89PNG"))
        status, body = self.request("/")
        self.assertIn(b'rel="icon"', body)
        self.assertIn(b"On-call notebook", body)
        with mock.patch.dict("os.environ", {"ONCALL_NOTES_DIR": str(Path(self.tmp.name) / "notes")}):
            from hub.core import oncall_notes
            oncall_notes.add("shift_start", source="hub")
            oncall_notes.add("note", "restarted the worker", source="laptop", monitors=["5 Outbox"])
            oncall_notes.add("note", "nothing else", source="laptop")
            status, body = self.request("/api/oncall")
            data = json.loads(body)
            self.assertEqual(status, 200)
            self.assertTrue(data["shift"]["on"])
            self.assertEqual(data["count"], 2)
            self.assertEqual([e["kind"] for e in data["entries"]], ["note", "note", "shift_start"])
            self.assertIn("PT", data["entries"][0]["when"])
            status, body = self.request("/api/oncall?q=worker")
            self.assertEqual([e["text"] for e in json.loads(body)["entries"]], ["restarted the worker"])
            # The note box sends `oncall note <text>`, which the on_call plugin answers (not loaded in this hub).
            status, body = self.post(["oncall", "note", "posted from the dashboard"], headers={"X-Hub-Token": self.token})
            self.assertEqual(status, 200)

    def test_posts_need_the_token_and_our_origin(self):
        self.assertEqual(self.post(["engine"])[0], 403)
        self.assertEqual(self.post(["engine"], {"X-Hub-Token": "wrong"})[0], 403)
        evil = {"X-Hub-Token": self.token, "Origin": "https://evil.example"}
        self.assertEqual(self.post(["engine"], evil)[0], 403)
        status, body = self.post(["engine"], {"X-Hub-Token": self.token, "Origin": self.base})
        self.assertEqual(status, 200)
        self.assertIn("agents run on", json.loads(body)["reply"])

    def test_token_is_not_given_to_other_sites_or_hosts(self):
        self.assertEqual(self.request("/api/token", headers={"Origin": "https://evil.example"})[0], 403)
        status, body = self.request("/api/token", headers={"Origin": self.base})
        self.assertEqual((status, json.loads(body)["token"]), (200, self.token))
        self.assertEqual(self.request("/api/state", headers={"Host": f"evil.example:{self.port}"})[0], 403)

    def test_bad_command_body(self):
        status, _ = self.request("/api/command", data=b"{}", method="POST",
                                 headers={"X-Hub-Token": self.token, "Content-Type": "application/json"})
        self.assertEqual(status, 400)

    def test_job_detail_and_live_tail(self):
        job = self.hub.store.enqueue("demo", "answer a question", {"x": 1})
        run_dir = Path(self.tmp.name) / "runs" / job.id
        run_dir.mkdir(parents=True)
        path = run_dir / "events.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in CODEX[:2]))
        status, body = self.request(f"/api/jobs/{job.id}")
        detail = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(detail["job"]["title"], "answer a question")
        self.assertEqual(detail["events"], ["Codex session t-1", "Running: /bin/zsh -lc 'gh pr view 1'"])
        with path.open("a") as f:
            f.write(json.dumps(CODEX[3]) + "\n")
        status, body = self.request(f"/api/jobs/{job.id}/events?offset={detail['offset']}")
        tail = json.loads(body)
        self.assertEqual(tail["lines"], ["Codex: the answer"])
        self.assertEqual(tail["status"], "queued")
        self.assertEqual(self.request("/api/jobs/nope")[0], 404)
        self.assertEqual(self.request("/api/jobs/../../etc/passwd")[0], 404)
        status, body = self.request("/api/jobs?status=queued&limit=5")
        self.assertEqual([j["id"] for j in json.loads(body)["jobs"]], [job.id])


if __name__ == "__main__":
    unittest.main()
