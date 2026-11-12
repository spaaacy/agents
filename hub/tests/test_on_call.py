"""Tests for the on_call plugin. Run from ~/.agents: PYTHONPATH=. hub/.venv/bin/python -m unittest hub.tests.test_on_call -v"""
from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

from hub.core import agent
from hub.core.store import Store
from hub.plugins.on_call import dd
from hub.plugins.on_call import plugin as oc

CFG: dict[str, Any] = {
    "owner_datadog_user_id": "me",
    "datadog_team_handle": "devs",
    "page_handle": "oncall-devs",
    "datadog_env_file": "/nonexistent",
    "poll_seconds": 60,
    "on_call_check_seconds": 300,
    "settle_seconds": 90,
    "repeat_quiet_minutes": 120,
    "catch_up_hours": 12,
    "timeout_seconds": 60,
    "allowed_tools": ["Read", "Grep"],
}


def event(event_id: str, when: str, monitor_id: int = 1, dest: str = "Alert", notified: list[str] | None = None,
          name: str = "[P2] Thing Broken") -> dict[str, Any]:
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


def page(event_id: str, monitor_id: int = 1, minute: int = 0) -> oc.Page:
    return oc.Page(event_id, datetime(2026, 11, 10, 17, minute, tzinfo=UTC), monitor_id, "[P2] X", "*", "OK", "Alert")


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


class FakeClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def chat_postMessage(self, **payload: Any) -> dict[str, Any]:
        self.posts.append(payload)
        return {"ts": f"ts{len(self.posts)}"}


class FakeSlack:
    def __init__(self, connected: bool = True) -> None:
        self.web = object() if connected else None
        self.bot_dm = "D1" if connected else None
        self.api = FakeClient()

    def client(self, as_user: bool = False) -> FakeClient:
        return self.api

    def permalink(self, channel: str, ts: str, *, as_user: bool = False) -> str:
        return f"https://slack/{channel}/{ts}"


class FakeHub:
    def __init__(self, root: Path, slack: Any = None) -> None:
        self.cfg = {"agent": {"workdir": str(root), "add_dirs": ["/tmp/a"], "mcp_servers": ["pg-readonly"]}}
        self.state_dir = root / "state"
        self.store = Store(root / "hub.sqlite3")
        self.slack = slack
        self.stopping = threading.Event()

    def wake(self) -> None:
        pass

    def slack_web(self) -> Any:
        return self.slack


class Base(unittest.TestCase):
    engine_name = "claude"

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.slack = FakeSlack()
        self.hub = FakeHub(self.root, self.slack)
        patches = [
            mock.patch.object(oc, "OLD_STATE_FILE", self.root / "old-state.json"),
            mock.patch.object(oc, "notify", lambda *args: None),
            mock.patch.object(oc.agent.engine, "engine", lambda *args: self.engine_name),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.plugin = oc.OnCall(self.hub, dict(CFG))

    def posts(self) -> list[dict[str, Any]]:
        return self.slack.api.posts


class PagesFromEvents(unittest.TestCase):
    def test_only_non_ok_states_that_notified_the_on_call_handle_are_pages(self) -> None:
        events = [
            event("a", "2026-11-10T17:47:07Z"),
            event("b", "2026-11-10T17:48:00Z", dest="OK"),
            event("c", "2026-11-10T17:49:00Z", notified=["slack-datadog-alerts"]),  # a [P3] or a warning
            event("d", "2026-11-10T17:40:00Z", dest="No Data", monitor_id=2),
        ]
        pages = oc.pages_from_events(events, "oncall-devs")
        self.assertEqual([p.event_id for p in pages], ["d", "a"])  # oldest first
        self.assertEqual(pages[0].dest, "No Data")
        self.assertEqual(pages[1].group, "*")

    def test_page_line_names_the_monitor_and_both_times(self) -> None:
        line = oc.pages_from_events([event("a", "2026-11-10T17:47:07Z")], "oncall-devs")[0].line()
        self.assertIn("9:47am PST", line)
        self.assertIn("17:47 UTC", line)
        self.assertIn("monitor 1", line)

    def test_a_page_survives_the_job_payload(self) -> None:
        original = page("a", 5, 3)
        self.assertEqual(oc.Page.from_dict(json.loads(json.dumps(original.to_dict()))), original)


class OnCallMatch(unittest.TestCase):
    def test_matches_by_datadog_user_id(self) -> None:
        self.assertTrue(oc.owner_on_call([{"type": "users", "id": "me"}], "me"))
        self.assertFalse(oc.owner_on_call([{"type": "users", "id": "you"}], "me"))
        self.assertFalse(oc.owner_on_call([], "me"))


class SplitPages(unittest.TestCase):
    def test_seen_pages_are_dropped_and_recent_monitors_are_repeats(self) -> None:
        state = oc.RunnerState(seen=["old"], investigated={"2": {"at": 1000.0, "channel": "D", "ts": "1.1"}})
        fresh, repeats = oc.split_pages([page("old", 1), page("new", 1), page("again", 2)], state,
                                        now=1000.0 + 60, quiet_minutes=120)
        self.assertEqual([p.event_id for p in fresh], ["new"])
        self.assertEqual([p.event_id for p in repeats], ["again"])

    def test_a_monitor_investigated_long_ago_is_investigated_again(self) -> None:
        state = oc.RunnerState(investigated={"2": {"at": 0.0}})
        fresh, repeats = oc.split_pages([page("x", 2)], state, now=3 * 3600, quiet_minutes=120)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(repeats, [])


class ReadWindow(unittest.TestCase):
    def test_reads_back_past_the_checkpoint_but_never_past_the_catch_up_limit(self) -> None:
        now = 100_000.0
        self.assertEqual(oc.read_window(oc.RunnerState(checkpoint=now - 60), now, 12), now - 60 - 300)
        self.assertEqual(oc.read_window(oc.RunnerState(checkpoint=0.0), now, 12), now - 12 * 3600)


class Poll(Base):
    def test_first_start_takes_only_pages_from_now_on(self) -> None:
        self.plugin.datadog = datadog = FakeDatadog(events=[])
        self.plugin.poll(now=50_000.0)
        self.assertEqual(self.plugin.state.checkpoint, 50_000.0)
        self.assertEqual(datadog.event_reads, [(50_000.0 - 300, 50_000.0)])
        self.assertEqual(self.plugin.get("state")["checkpoint"], 50_000.0)  # kept across restarts

    def test_off_shift_reads_no_events_and_moves_the_checkpoint(self) -> None:
        self.plugin.datadog = datadog = FakeDatadog(on_call=False, events=[event("a", "2026-11-10T17:47:07Z")])
        self.plugin.poll(now=50_000.0)
        self.assertEqual(datadog.event_reads, [])
        self.assertEqual(self.plugin.pending, [])

    def test_new_pages_settle_then_go_out_as_one_job(self) -> None:
        self.plugin.datadog = FakeDatadog(events=[event("a", "2026-11-10T17:47:07Z", 1), event("b", "2026-11-10T17:47:30Z", 2)])
        self.plugin.poll(now=50_000.0)
        self.assertIsNone(self.plugin.flush(now=50_030.0))  # still settling
        job = self.plugin.flush(now=50_100.0)
        self.assertEqual([p["event_id"] for p in job.payload["pages"]], ["a", "b"])
        self.assertEqual(job.plugin, "on_call")
        self.assertEqual(job.priority, 90)
        self.plugin.poll(now=50_160.0)  # the same events read again
        self.assertEqual(self.plugin.pending, [])

    def test_pages_wait_while_an_investigation_is_queued(self) -> None:
        self.hub.store.enqueue("on_call", "earlier", {"pages": []}, priority=90)
        self.plugin.datadog = FakeDatadog(events=[event("a", "2026-11-10T17:47:07Z", 1)])
        self.plugin.poll(now=50_000.0)
        self.assertIsNone(self.plugin.flush(now=50_200.0))
        self.assertEqual(len(self.plugin.pending), 1)

    def test_a_repeat_page_is_a_line_in_the_earlier_thread(self) -> None:
        self.plugin.datadog = FakeDatadog(events=[event("again", "2026-11-10T17:47:07Z", 7)])
        self.plugin.load_state()
        self.plugin.state.checkpoint = 49_000.0
        self.plugin.state.investigated["7"] = {"at": 49_500.0, "channel": "D1", "ts": "123.4"}
        self.plugin.poll(now=50_000.0)
        self.assertEqual(self.plugin.pending, [])
        note = self.posts()[0]
        self.assertEqual((note["channel"], note["thread_ts"]), ("D1", "123.4"))
        self.assertIn("paged again", note["markdown_text"])
        self.assertTrue(note["reply_broadcast"])  # shown in the DM too, not only in the old thread


class Migration(Base):
    def test_the_old_runner_state_is_carried_over_once(self) -> None:
        old = {"checkpoint": 123.0, "seen": ["e1"], "investigated": {"9": {"at": 1.0, "channel": "D1", "ts": "1.2"}}}
        (self.root / "old-state.json").write_text(json.dumps(old))
        self.plugin.load_state()
        self.assertEqual(self.plugin.state.checkpoint, 123.0)
        self.assertEqual(self.plugin.state.investigated["9"]["ts"], "1.2")
        (self.root / "old-state.json").write_text(json.dumps({**old, "checkpoint": 999.0}))
        again = oc.OnCall(self.hub, dict(CFG))
        again.load_state()
        self.assertEqual(again.state.checkpoint, 123.0)  # never read twice


class Prompt(unittest.TestCase):
    def test_skill_frontmatter_is_dropped_and_arguments_are_the_monitors(self) -> None:
        skill_file = Path(tempfile.mkdtemp()) / "SKILL.md"
        skill_file.write_text("---\nname: on-call\ndescription: x\n---\n\nTarget: `$ARGUMENTS`.\nBody.")
        skill = oc.skill_text(skill_file)
        self.assertTrue(skill.startswith("Target:"))
        prompt = oc.build_prompt([page("a", 9), page("b", 3)], skill)
        self.assertIn("Target: `3 9`.", prompt)
        self.assertIn("Ask nothing", prompt)
        self.assertIn("monitor 9", prompt)

    def test_the_prompt_ends_with_his_voice_rules(self) -> None:
        prompt = oc.build_prompt([page("a", 9)], "Target: `$ARGUMENTS`.")
        self.assertIn("Voice (standing rule):", prompt)
        self.assertGreater(prompt.index("Voice (standing rule):"), prompt.index("</skill>"))

    def test_the_real_skill_reads(self) -> None:
        self.assertIn("## Report", oc.skill_text())

    def test_points_at_dd_py_in_this_folder_and_states_each_engines_limits(self) -> None:
        claude = oc.build_prompt([page("a", 9)], "x", "claude")
        codex = oc.build_prompt([page("a", 9)], "x", "codex")
        self.assertIn(str(oc.HERE / "dd.py"), claude)
        self.assertTrue((oc.HERE / "dd.py").exists())
        self.assertIn("chained command is refused whole", claude)
        self.assertNotIn("chained command", codex)
        self.assertIn("Files are read-only", codex)
        self.assertIn(f"Bash(python3 {oc.HERE / 'dd.py'} *)", oc.dd_allowed_tools())

    def test_settings_deny_writes_and_allow_list_has_no_write_tools(self) -> None:
        settings = json.loads(oc.SETTINGS_FILE.read_text())
        for denied in ("Edit", "Write", "Bash(git push *)", "Bash(az *)", "mcp__claude_ai_Slack"):
            self.assertIn(denied, settings["permissions"]["deny"])
        allowed = json.loads((oc.HERE / "config.json").read_text())["allowed_tools"]
        self.assertFalse([tool for tool in allowed if tool.split("(")[0] in ("Edit", "Write")])
        self.assertFalse([tool for tool in allowed if tool == "WebFetch"])  # only named status pages
        self.assertFalse([tool for tool in allowed if "on-call-runner" in tool])


class Footer(unittest.TestCase):
    def test_codex_footer_resumes_codex_and_counts_tokens(self) -> None:
        footer = oc.report_footer(agent.AgentResult("codex", "x", session="codex:t1", tokens=1234))
        self.assertIn("codex resume t1", footer)
        self.assertIn("1,234 tokens", footer)

    def test_claude_footer_resumes_claude_with_cost(self) -> None:
        footer = oc.report_footer(agent.AgentResult("claude", "x", session="claude:s1", cost_usd=1.5))
        self.assertIn("claude --resume s1", footer)
        self.assertIn("$1.50", footer)


class Handle(Base):
    def run_agent(self, text: str = "real: thing broke", error: str | None = None, engine_name: str = "claude"):
        self.tasks: list[agent.AgentTask] = []

        def fake(task: agent.AgentTask) -> agent.AgentResult:
            self.tasks.append(task)
            return agent.AgentResult(engine_name, "" if error else text, session=f"{engine_name}:s1", error=error,
                                     cost_usd=1.5 if engine_name == "claude" else None,
                                     tokens=900 if engine_name == "codex" else None)

        return fake

    def test_posts_a_headline_then_the_report_in_its_thread(self) -> None:
        with mock.patch.dict("os.environ", {"DD_API_KEY": "k-api", "DD_APP_KEY": "k-app"}):
            result = self.plugin.investigate([page("a", 5)], self.run_agent())
        self.assertEqual(result.status, "done")
        headline, report = self.posts()
        self.assertIn("datadog paged you", headline["markdown_text"])
        self.assertNotIn("thread_ts", headline)
        self.assertEqual(report["thread_ts"], "ts1")
        self.assertIn("real: thing broke", report["markdown_text"])
        self.assertIn("claude --resume s1", report["markdown_text"])
        self.assertEqual(result.link, "https://slack/D1/ts2")
        self.assertEqual(result.summary, "real: thing broke")
        self.assertEqual(self.plugin.state.investigated["5"]["ts"], "ts1")
        task = self.tasks[0]
        self.assertEqual(task.env, {"DD_API_KEY": "k-api", "DD_APP_KEY": "k-app"})
        self.assertNotIn("k-api", task.prompt)
        self.assertEqual(task.engine, "claude")
        self.assertIn("linear", task.mcp_servers)
        self.assertIn("pg-readonly", task.mcp_servers)
        self.assertEqual(task.claude_settings, oc.SETTINGS_FILE)
        self.assertIn(f"Bash(python3 {oc.HERE / 'dd.py'} *)", task.claude_allowed_tools)
        self.assertIn("chained command", task.prompt)

    def test_codex_gets_its_limits_and_its_resume_command(self) -> None:
        self.engine_name = "codex"
        result = self.plugin.investigate([page("a", 5)], self.run_agent(engine_name="codex"))
        self.assertEqual(self.tasks[0].engine, "codex")
        self.assertIn("Files are read-only", self.tasks[0].prompt)
        self.assertIn("codex resume s1", self.posts()[1]["markdown_text"])
        self.assertEqual((result.engine, result.tokens), ("codex", 900))

    def test_a_failed_run_says_so_in_the_thread(self) -> None:
        result = self.plugin.investigate([page("a", 5)], self.run_agent(error="boom"))
        self.assertEqual((result.status, result.error), ("failed", "boom"))
        self.assertIn("the investigation failed: boom", self.posts()[1]["markdown_text"])

    def test_without_slack_the_report_is_kept_and_the_job_says_it_was_not_posted(self) -> None:
        self.hub.slack = None
        result = self.plugin.investigate([page("a", 5)], self.run_agent("line one\nline two"))
        self.assertEqual(result.status, "failed")
        self.assertIn("line two", result.summary)
        self.assertIn("isn't connected", result.error)

    def test_without_post_nothing_reaches_slack(self) -> None:
        result = self.plugin.investigate([page("a", 5)], self.run_agent(), post=False)
        self.assertEqual(self.posts(), [])
        self.assertEqual(result.status, "done")

    def test_handle_runs_a_queued_job_through_the_context(self) -> None:
        job = self.hub.store.enqueue("on_call", "x", {"pages": [page("a", 5).to_dict()]}, priority=90)
        ctx = mock.Mock()
        ctx.run_agent = self.run_agent()
        result = self.plugin.handle(job, ctx)
        self.assertEqual(result.status, "done")
        ctx.note.assert_called_once()


class DatadogHelper(unittest.TestCase):
    def test_metric_prints_min_max_last_without_the_keys(self) -> None:
        class Response(io.BytesIO):
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        body = {"series": [{"scope": "env:production",
                            "pointlist": [[1794330000000, 2.0], [1794330060000, 9.0], [1794330120000, 4.0]]}]}
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


class EnvFile(unittest.TestCase):
    def test_reads_key_value_lines(self) -> None:
        path = Path(tempfile.mkdtemp()) / "x.env"
        path.write_text("# c\nexport DD_API_KEY='a'\nDD_APP_KEY=b\n\n")
        self.assertEqual(oc.load_env_file(path), {"DD_API_KEY": "a", "DD_APP_KEY": "b"})
        self.assertEqual(oc.datadog_keys({"datadog_env_file": str(path)}, environ={}), ("a", "b"))


if __name__ == "__main__":
    unittest.main()


REPORT_WITH_FOLLOWUPS = """real: the latency monitor fired on a deploy spike, cleared in 4m

Follow-ups:
1. Tune `p2-api-latency` with /tune-monitor: widen the window to 10m
2. Draft a ticket for the slow **carrier search** query
   (the one behind [the trace](https://x/y))
3. Start the incident report
"""


class FollowupParsing(unittest.TestCase):
    def test_numbered_lines_after_the_heading_with_continuations(self) -> None:
        items = oc.parse_followups(REPORT_WITH_FOLLOWUPS)
        self.assertEqual(len(items), 3)
        self.assertIn("(the one behind", items[1])
        self.assertTrue(items[0].startswith("Tune `p2-api-latency`"))

    def test_numbered_findings_without_a_heading_are_not_follow_ups(self) -> None:
        self.assertEqual(oc.parse_followups("1. a finding\n2. another"), [])
        self.assertEqual(oc.parse_followups("Follow-ups: none needed, it cleared."), [])

    def test_the_last_heading_wins_and_the_list_stops_at_prose(self) -> None:
        text = "Next steps for you:\n1. old\n\nmore prose\n\n**Follow-ups**\n1. new one\nprose after"
        self.assertEqual(oc.parse_followups(text), ["new one"])

    def test_labels_are_plain_and_short(self) -> None:
        label = oc.followup_label(2, "Draft a ticket for the slow **carrier search** query [trace](https://x) " * 3)
        self.assertTrue(label.startswith("Do 2: Draft a ticket for the slow carrier search"))
        self.assertLessEqual(len(label), 60)
        self.assertNotIn("*", label)


class FakeActions:
    def __init__(self) -> None:
        self.offered: list[tuple] = []

    def button(self, plugin: str, name: str, label: str, args: dict, *, style=None, confirm=None) -> dict:
        self.offered.append((plugin, name, label, args, confirm))
        return {"type": "button", "action_id": f"act:{len(self.offered)}", "value": str(len(self.offered))}

    @staticmethod
    def blocks(text: str, buttons: list) -> list:
        return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}, {"type": "actions", "elements": buttons}]


class MarkdownSlack(FakeSlack):
    def post_markdown(self, channel: str, text: str, thread_ts: str | None = None, *, as_user: bool = False,
                      blocks: list | None = None) -> list[str]:
        payload = {"channel": channel, "text": text, "thread_ts": thread_ts, "blocks": blocks}
        return [self.api.chat_postMessage(**payload)["ts"]]


class FollowupButtons(Handle):
    def setUp(self) -> None:
        super().setUp()
        self.slack = MarkdownSlack()
        self.hub.slack = self.slack
        self.hub.actions = FakeActions()

    def test_each_follow_up_gets_a_button_that_resumes_the_session_with_the_keys(self) -> None:
        with mock.patch.dict("os.environ", {"DD_API_KEY": "k-api", "DD_APP_KEY": "k-app"}):
            result = self.plugin.investigate([page("a", 5)], self.run_agent(text=REPORT_WITH_FOLLOWUPS))
        self.assertEqual(result.status, "done")
        headline, report, buttons = self.posts()
        self.assertIn("Follow-ups:", report["markdown_text"])  # the report keeps its own markdown message
        self.assertEqual(buttons["thread_ts"], "ts1")
        self.assertEqual(len(buttons["blocks"][1]["elements"]), 3)
        plugin, name, label, args, confirm = self.hub.actions.offered[0]
        self.assertEqual((plugin, name), ("on_call", "agent"))
        self.assertTrue(label.startswith("Do 1: Tune p2-api-latency"))
        self.assertEqual(args["session"], "claude:s1")
        self.assertEqual(args["env"], {"DD_API_KEY": "k-api", "DD_APP_KEY": "k-app"})
        self.assertIn("Carry out follow-up 1", args["instruction"])
        self.assertIn("/tune-monitor", confirm)

    def test_no_follow_ups_or_a_failed_run_means_no_buttons(self) -> None:
        self.plugin.investigate([page("a", 5)], self.run_agent(text="real: cleared, nothing to do"))
        self.plugin.investigate([page("b", 6)], self.run_agent(error="boom"))
        self.assertEqual(self.hub.actions.offered, [])

    def test_a_tapped_follow_up_runs_as_an_approved_agent_action(self) -> None:
        self.engine_name = "codex"  # the session it resumes is a Codex one
        job = mock.Mock(payload={"reply": {"channel": "D1", "thread_ts": "ts1"}})
        seen: list[agent.AgentTask] = []
        ctx = mock.Mock()
        ctx.run_agent.side_effect = lambda task: seen.append(task) or agent.AgentResult(
            "codex", "opened the draft PR https://github.com/x/pull/1", session="codex:s9")
        out = self.plugin.act("agent", {"instruction": "Tune it", "session": "codex:s1", "env": {"DD_API_KEY": "k"}},
                              job, ctx)
        self.assertEqual(out.status, "done")
        task = seen[0]
        self.assertEqual((task.mode, task.session, task.env), ("act", "codex:s1", {"DD_API_KEY": "k"}))
        self.assertTrue(task.prompt.startswith(agent.ACT_PREAMBLE))
        self.assertTrue(task.prompt.endswith("Tune it"))
        self.assertIn("opened the draft PR", self.posts()[-1]["text"])
