import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from hub import mcp_server
from hub.core import oncall_notes as notes

T0 = datetime(2026, 11, 16, 17, 0, tzinfo=UTC)


class Notebook(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"ONCALL_NOTES_DIR": str(self.dir)})
        env.start()
        self.addCleanup(env.stop)

    def test_the_real_start_corrects_a_late_mark(self):
        notes.mark_shift(True)  # noticed at hub start
        self.assertIsNotNone(notes.mark_shift(True, start=T0))  # the schedule says it began earlier
        self.assertIsNone(notes.mark_shift(True, start=T0))
        self.assertEqual(notes.shift(notes.entries()), (T0, None))
        notes.mark_shift(False)
        notes.mark_shift(True, start=T0 + timedelta(days=14))
        self.assertEqual(notes.shift(notes.entries()), (T0 + timedelta(days=14), None), "a new shift after an end")

    def test_shifts_are_marked_only_when_they_change(self):
        self.assertIsNotNone(notes.mark_shift(True))
        self.assertIsNone(notes.mark_shift(True))
        self.assertTrue(notes.on_call())
        self.assertIsNotNone(notes.mark_shift(False))
        self.assertIsNone(notes.mark_shift(False))
        self.assertFalse(notes.on_call())

    def test_digest_puts_this_shift_first_then_the_same_monitor_then_the_rest(self):
        notes.add("investigation", "outbox page: throttled demo mailbox", source="Dot", monitors=["5 Outbox stalled"],
                  at=T0 - timedelta(days=7))
        notes.add("investigation", "sync page: Cooler token revoked", source="Dot", monitors=["9 Mailbox sync"],
                  at=T0 - timedelta(days=3))
        notes.add("shift_start", source="hub", at=T0)
        notes.add("note", "restarted the worker at 9:10", source="laptop", monitors=["5 Outbox stalled"],
                  at=T0 + timedelta(hours=1))
        notes.add("investigation", "real: sends failing since 9:05 deploy", source="Dot", monitors=["5 Outbox stalled"],
                  at=T0 + timedelta(hours=2))
        text = notes.digest(monitors=["5"])
        self.assertTrue(text.startswith("This shift (on call since"))
        self.assertLess(text.index("sends failing"), text.index("restarted the worker"), "newest first")
        self.assertIn("Earlier shifts, about these monitors:", text)
        self.assertLess(text.index("Earlier shifts, about"), text.index("throttled demo mailbox"))
        self.assertIn("other recent findings", text)
        self.assertIn("Cooler token revoked", text)

    def test_a_busy_shift_puts_the_paging_monitor_first(self):
        notes.add("shift_start", source="hub", at=T0)
        notes.add("investigation", "outbox: demo mailbox throttled", source="Dot", monitors=["5 Outbox"],
                  at=T0 + timedelta(minutes=1))
        for i in range(30):
            notes.add("investigation", f"other page {i}\n" + "detail " * 200, source="Dot", monitors=[f"{100 + i} Other"],
                      at=T0 + timedelta(hours=1, minutes=i))
        text = notes.digest(monitors=["5"])
        self.assertLess(text.index("About these monitors:"), text.index("demo mailbox throttled"))
        self.assertLess(text.index("demo mailbox throttled"), text.index("other page 29"))
        self.assertIn("Earlier this shift:", text)
        self.assertLessEqual(len(text), 8000)

    def test_off_shift_the_last_shift_is_shown(self):
        notes.add("shift_start", source="hub", at=T0)
        notes.add("note", "quiet shift", source="laptop", at=T0 + timedelta(hours=1))
        notes.add("shift_end", source="hub", at=T0 + timedelta(hours=8))
        text = notes.digest()
        self.assertTrue(text.startswith("Last shift"))
        self.assertIn("quiet shift", text)

    def test_search_and_render(self):
        notes.add("note", "DAT account locked for Action Heavy Haul", source="laptop")
        notes.add("note", "nothing to see", source="laptop")
        self.assertEqual([e["text"] for e in notes.search("dat locked")], ["DAT account locked for Action Heavy Haul"])
        self.assertIn("DAT account locked", (self.dir / "notebook.md").read_text())

    def test_empty_notes_and_unknown_kinds_are_refused(self):
        with self.assertRaises(ValueError):
            notes.add("note", "  ", source="x")
        with self.assertRaises(ValueError):
            notes.add("gossip", "x", source="x")
        self.assertIn("usage", notes.command(["note"], source="x"))

    def test_a_bad_line_is_skipped(self):
        notes.add("note", "kept", source="x")
        with (self.dir / "notes.jsonl").open("a") as f:
            f.write("not json\n")
        self.assertEqual([e["text"] for e in notes.entries()], ["kept"])

    def test_mcp_tools_read_and_write_it_without_the_hub(self):
        server = mcp_server.Server()
        saved = server.call("oncall_note", {"text": "ruled out the deploy", "monitors": ["5 Outbox"], "investigation": True})
        self.assertIn("saved to the on-call notebook", saved)
        entry = notes.entries()[-1]
        self.assertEqual((entry["kind"], entry["monitors"]), ("investigation", ["5 Outbox"]))
        self.assertIn("ruled out the deploy", server.call("oncall_notes", {"monitors": ["5"]}))
        self.assertIn("ruled out the deploy", server.call("oncall_notes", {"query": "deploy"}))
        reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "oncall_note", "arguments": {"text": ""}}})
        self.assertTrue(reply["result"]["isError"])
        names = [t["name"] for t in server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
        self.assertIn("oncall_notes", names)


if __name__ == "__main__":
    unittest.main()
