"""Tests for the on-call runner. Run: python3 -m unittest -v test_runner.py"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

import runner
import watch

REAL_RUN_CLAUDE = runner.run_claude

CFG: dict[str, Any] = {
    "owner_datadog_user_id": "me",
    "owner_slack_id": "U1",
    "datadog_team_handle": "devs",
    "page_handle": "oncall-devs",
    "datadog_env_file": "/nonexistent",
    "workdir": "/tmp",
    "add_dirs": ["/tmp/a"],
    "mcp_servers": [],
    "poll_seconds": 60,
    "on_call_check_seconds": 300,
    "settle_seconds": 90,
    "repeat_quiet_minutes": 120,
    "catch_up_hours": 12,
    "timeout_seconds": 60,
    "allowed_tools": ["Read", "Grep"],
}


def event(
    event_id: str,
    when: str,
    monitor_id: int = 1,
    dest: str = "Alert",
    notified: list[str] | None = None,
    name: str = "[P2] Thing Broken",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "attributes": {
            "timestamp": when,
            "attributes": {
                "monitor": {
                    "id": monitor_id,
                    "name": name,
                    "groups": [],
                    "transition": {"source_state": "OK", "destination_state": dest},
                    "notifications": ["oncall-devs", "slack-datadog-alerts"] if notified is None else notified,
                }
            },
        },
    }


def page(event_id: str, monitor_id: int = 1, minute: int = 0) -> runner.Page:
    return runner.Page(event_id, datetime(2026, 11, 10, 17, minute, tzinfo=UTC), monitor_id, "[P2] X", "*", "OK", "Alert")


class FakeDatadog:
    def __init__(self, on_call: bool = True, events: list[dict[str, Any]] | None = None) -> None:
        self.on_call = on_call
        self.events = events or []
        self.event_reads: list[tuple[float, float]] = []

    def team_id(self, handle: str) -> str:
        return "team"

    def on_call_users(self, team_id: str) -> list[dict[str, Any]]:
        return [{"type": "users", "id": "me" if self.on_call else "someone-else"}]

    def alert_events(self, since: float, until: float) -> list[dict[str, Any]]:
        self.event_reads.append((since, until))
        return self.events


class FakeSlack:
    def __init__(self) -> None:
        self.posts: list[tuple[str, str, str | None]] = []
        self.broadcasts: list[bool] = []

    def post(self, channel: str, text: str, thread_ts: str | None = None, broadcast: bool = False) -> str:
        self.posts.append((channel, text, thread_ts))
        self.broadcasts.append(broadcast)
        return f"ts{len(self.posts)}"

    def permalink(self, channel: str, ts: str) -> str:
        return f"https://slack/{channel}/{ts}"


class StateIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        patches = [
            mock.patch.object(runner, "STATE", self.tmp),
            mock.patch.object(runner, "STATE_FILE", self.tmp / "state.json"),
            mock.patch.object(runner, "RUNS_DIR", self.tmp / "runs"),
            mock.patch.object(runner, "MCP_FILE", self.tmp / "mcp.json"),
            mock.patch.object(runner, "notify", lambda *args: None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        # RunnerState.load/save defaults were bound at import; point them at the temp folder.
        load, save = runner.RunnerState.load.__func__, runner.RunnerState.save
        patch_load = mock.patch.object(
            runner.RunnerState, "load", classmethod(lambda cls, path=None: load(cls, self.tmp / "state.json"))
        )
        patch_save = mock.patch.object(
            runner.RunnerState, "save", lambda state, path=None: save(state, self.tmp / "state.json")
        )
        for patch in (patch_load, patch_save):
            patch.start()
            self.addCleanup(patch.stop)


class PagesFromEvents(unittest.TestCase):
    def test_only_non_ok_states_that_notified_the_on_call_handle_are_pages(self) -> None:
        events = [
            event("a", "2026-11-10T17:47:07Z"),
            event("b", "2026-11-10T17:48:00Z", dest="OK"),
            event("c", "2026-11-10T17:49:00Z", notified=["slack-datadog-alerts"]),  # a [P3] or a warning
            event("d", "2026-11-10T17:40:00Z", dest="No Data", monitor_id=2),
        ]
        pages = runner.pages_from_events(events, "oncall-devs")
        self.assertEqual([p.event_id for p in pages], ["d", "a"])  # oldest first
        self.assertEqual(pages[1].monitor_id, 1)
        self.assertEqual(pages[0].dest, "No Data")
        self.assertEqual(pages[1].group, "*")

    def test_page_line_names_the_monitor_and_both_times(self) -> None:
        line = runner.pages_from_events([event("a", "2026-11-10T17:47:07Z")], "oncall-devs")[0].line()
        self.assertIn("9:47am PST", line)
        self.assertIn("17:47 UTC", line)
        self.assertIn("monitor 1", line)


class OnCall(unittest.TestCase):
    def test_matches_by_datadog_user_id(self) -> None:
        self.assertTrue(runner.owner_on_call([{"type": "users", "id": "me"}], "me"))
        self.assertFalse(runner.owner_on_call([{"type": "users", "id": "you"}], "me"))
        self.assertFalse(runner.owner_on_call([], "me"))


class SplitPages(unittest.TestCase):
    def test_seen_pages_are_dropped_and_recent_monitors_are_repeats(self) -> None:
        state = runner.RunnerState(seen=["old"], investigated={"2": {"at": 1000.0, "channel": "D", "ts": "1.1"}})
        fresh, repeats = runner.split_pages(
            [page("old", 1), page("new", 1), page("again", 2)], state, now=1000.0 + 60, quiet_minutes=120
        )
        self.assertEqual([p.event_id for p in fresh], ["new"])
        self.assertEqual([p.event_id for p in repeats], ["again"])

    def test_a_monitor_investigated_long_ago_is_investigated_again(self) -> None:
        state = runner.RunnerState(investigated={"2": {"at": 0.0}})
        fresh, repeats = runner.split_pages([page("x", 2)], state, now=3 * 3600, quiet_minutes=120)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(repeats, [])


class ReadWindow(unittest.TestCase):
    def test_reads_back_past_the_checkpoint_but_never_past_the_catch_up_limit(self) -> None:
        now = 100_000.0
        self.assertEqual(runner.read_window(runner.RunnerState(checkpoint=now - 60), now, 12), now - 60 - 300)
        self.assertEqual(runner.read_window(runner.RunnerState(checkpoint=0.0), now, 12), now - 12 * 3600)


class Poll(StateIsolation):
    def test_first_start_takes_only_pages_from_now_on(self) -> None:
        datadog = FakeDatadog(events=[])
        r = runner.Runner(CFG, datadog, None, None)
        r.poll(now=50_000.0)
        self.assertEqual(r.state.checkpoint, 50_000.0)
        self.assertEqual(datadog.event_reads, [(50_000.0 - 300, 50_000.0)])

    def test_off_shift_reads_no_events_and_moves_the_checkpoint(self) -> None:
        datadog = FakeDatadog(on_call=False, events=[event("a", "2026-11-10T17:47:07Z")])
        r = runner.Runner(CFG, datadog, None, None)
        r.poll(now=50_000.0)
        self.assertEqual(datadog.event_reads, [])
        self.assertEqual(r.pending, [])

    def test_new_pages_wait_to_settle_then_go_out_as_one_batch(self) -> None:
        datadog = FakeDatadog(
            events=[event("a", "2026-11-10T17:47:07Z", 1), event("b", "2026-11-10T17:47:30Z", 2)]
        )
        r = runner.Runner(CFG, datadog, None, None)
        r.poll(now=50_000.0)
        self.assertEqual(r.take_batch(now=50_030.0), [])  # still settling
        batch = r.take_batch(now=50_100.0)
        self.assertEqual([p.event_id for p in batch], ["a", "b"])
        r.poll(now=50_160.0)  # the same events read again
        self.assertEqual(r.pending, [])

    def test_a_repeat_page_is_a_line_in_the_earlier_thread(self) -> None:
        slack = FakeSlack()
        datadog = FakeDatadog(events=[event("again", "2026-11-10T17:47:07Z", 7)])
        r = runner.Runner(CFG, datadog, slack, "D1")  # type: ignore[arg-type]
        r.state.checkpoint = 49_000.0
        r.state.investigated["7"] = {"at": 49_500.0, "channel": "D1", "ts": "123.4"}
        r.poll(now=50_000.0)
        self.assertEqual(r.pending, [])
        self.assertEqual(slack.posts[0][0], "D1")
        self.assertEqual(slack.posts[0][2], "123.4")
        self.assertIn("paged again", slack.posts[0][1])
        self.assertTrue(slack.broadcasts[0])  # shown in the DM too, not only in the old thread


class Prompt(unittest.TestCase):
    def test_skill_frontmatter_is_dropped_and_arguments_are_the_monitors(self) -> None:
        skill_file = Path(tempfile.mkdtemp()) / "SKILL.md"
        skill_file.write_text("---\nname: on-call\ndescription: x\n---\n\nTarget: `$ARGUMENTS`.\nBody.")
        skill = runner.skill_text(skill_file)
        self.assertTrue(skill.startswith("Target:"))
        prompt = runner.build_prompt([page("a", 9), page("b", 3)], skill)
        self.assertIn("Target: `3 9`.", prompt)
        self.assertIn("Ask nothing", prompt)
        self.assertIn("monitor 9", prompt)

    def test_the_prompt_ends_with_his_voice_rules(self) -> None:
        prompt = runner.build_prompt([page("a", 9)], "Target: `$ARGUMENTS`.")
        self.assertIn("Voice (standing rule):", prompt)
        self.assertGreater(prompt.index("Voice (standing rule):"), prompt.index("</skill>"))

    def test_the_real_skill_reads(self) -> None:
        self.assertIn("## Report", runner.skill_text())


class Command(unittest.TestCase):
    def test_runs_read_only_with_no_saved_settings_and_the_allow_list_last(self) -> None:
        cmd = runner.claude_command(CFG, None)
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(cmd[cmd.index("--allowedTools") :], ["--allowedTools", "Read", "Grep"])
        self.assertIn("/tmp/a", cmd)

    def test_settings_deny_writes_and_allow_list_has_no_write_tools(self) -> None:
        settings = json.loads(runner.SETTINGS_FILE.read_text())
        for denied in ("Edit", "Write", "Bash(git push *)", "Bash(az *)", "mcp__claude_ai_Slack"):
            self.assertIn(denied, settings["permissions"]["deny"])
        allowed = json.loads(runner.CONFIG_FILE.read_text())["allowed_tools"]
        self.assertFalse([tool for tool in allowed if tool.split("(")[0] in ("Edit", "Write")])
        self.assertFalse([tool for tool in allowed if tool == "WebFetch"])  # only named status pages


class Investigate(StateIsolation):
    def fake_claude(self, text: str = "real: thing broke", error: bool = False) -> Any:
        def execute(cmd: list[str], prompt: str, cwd: Path, timeout: int, on_line: Any) -> tuple[int, str]:
            self.prompt = prompt
            on_line(json.dumps({"type": "result", "result": text, "session_id": "s1", "total_cost_usd": 1.5, "is_error": error}))
            return (1 if error else 0), ""

        return execute

    def test_posts_a_headline_then_the_report_in_its_thread(self) -> None:
        slack = FakeSlack()
        r = runner.Runner(CFG, FakeDatadog(), slack, "D1")  # type: ignore[arg-type]
        with mock.patch.object(runner, "run_claude") as run:
            run.side_effect = lambda cfg, prompt, events: REAL_RUN_CLAUDE(cfg, prompt, events, self.fake_claude())
            report = r.investigate([page("a", 5)])
        self.assertIsNone(report.error)
        self.assertIn("datadog paged you", slack.posts[0][1])
        self.assertIsNone(slack.posts[0][2])
        self.assertEqual(slack.posts[1][2], "ts1")
        self.assertIn("real: thing broke", slack.posts[1][1])
        self.assertIn("claude --resume s1", slack.posts[1][1])
        self.assertEqual(r.state.investigated["5"]["ts"], "ts1")
        run_json = json.loads(next((self.tmp / "runs").glob("*/run.json")).read_text())
        self.assertEqual(run_json["status"], "done")
        self.assertEqual(run_json["session_id"], "s1")

    def test_a_failed_run_says_so_in_the_thread(self) -> None:
        slack = FakeSlack()
        r = runner.Runner(CFG, FakeDatadog(), slack, "D1")  # type: ignore[arg-type]
        with mock.patch.object(runner, "run_claude") as run:
            run.side_effect = lambda cfg, prompt, events: REAL_RUN_CLAUDE(
                cfg, prompt, events, self.fake_claude("boom", error=True)
            )
            report = r.investigate([page("a", 5)])
        self.assertEqual(report.error, "boom")
        self.assertIn("the investigation failed: boom", slack.posts[1][1])

    def test_once_without_post_writes_nothing_to_slack(self) -> None:
        slack = FakeSlack()
        r = runner.Runner(CFG, FakeDatadog(), slack, "D1")  # type: ignore[arg-type]
        with mock.patch.object(runner, "run_claude") as run:
            run.side_effect = lambda cfg, prompt, events: REAL_RUN_CLAUDE(cfg, prompt, events, self.fake_claude())
            r.investigate([page("a", 5)], post=False)
        self.assertEqual(slack.posts, [])
        self.assertNotIn("5", r.state.investigated)


class EnvFile(unittest.TestCase):
    def test_reads_key_value_lines(self) -> None:
        path = Path(tempfile.mkdtemp()) / "keys.env"
        path.write_text("# keys\nDD_API_KEY=abc\nexport DD_APP_KEY='def'\n\n")
        self.assertEqual(runner.load_env_file(path), {"DD_API_KEY": "abc", "DD_APP_KEY": "def"})


class WatchView(unittest.TestCase):
    def test_health_line(self) -> None:
        now = 1000.0
        self.assertIn("not started", watch.health_line(None, now))
        self.assertIn("looks stopped", watch.health_line({"heartbeat_at": 0}, now))
        line = watch.health_line({"heartbeat_at": now, "on_call": True, "pending": 2}, now)
        self.assertIn("you're on call", line)
        self.assertIn("2 pages waiting", line)


class DatadogHelper(unittest.TestCase):
    def test_the_prompt_points_unattended_runs_at_the_helper(self) -> None:
        prompt = runner.build_prompt([page("a", 9)], "Target: `$ARGUMENTS`.")
        self.assertIn(str(runner.HERE / "dd.py"), prompt)
        allowed = json.loads(runner.CONFIG_FILE.read_text())["allowed_tools"]
        self.assertIn(f"Bash(python3 {runner.HERE / 'dd.py'} *)", allowed)

    def test_metric_prints_min_max_last_without_the_keys(self) -> None:
        import io
        from contextlib import redirect_stdout

        import dd

        class Response(io.BytesIO):
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        body = {"series": [{"scope": "env:production", "pointlist": [[1794330000000, 2.0], [1794330060000, 9.0], [1794330120000, 4.0]]}]}
        seen: list[Any] = []

        def opener(request: Any) -> Response:
            seen.append(request)
            return Response(json.dumps(body).encode())

        with mock.patch.dict("os.environ", {"DD_API_KEY": "secret-api", "DD_APP_KEY": "secret-app"}):
            client = dd.Client(opener)
            out = io.StringIO()
            with redirect_stdout(out):
                dd.cmd_metric(client, "max:x{env:production}", 60)
        self.assertIn("min 2, max 9, last 4", out.getvalue())
        self.assertNotIn("secret", out.getvalue())
        self.assertEqual(seen[0].get_method(), "GET")


class Permalink(unittest.TestCase):
    def test_asks_with_query_arguments_not_a_json_body(self) -> None:
        import io

        class Response(io.BytesIO):
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        seen: list[Any] = []

        def opener(request: Any) -> Response:
            seen.append(request)
            return Response(json.dumps({"ok": True, "permalink": "https://x/p1"}).encode())

        self.assertEqual(runner.Slack("t", opener).permalink("D1", "1.2"), "https://x/p1")
        self.assertIn("channel=D1", seen[0].full_url)
        self.assertIsNone(seen[0].data)


if __name__ == "__main__":
    unittest.main()
