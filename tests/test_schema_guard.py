import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "hooks/schema-guard.py"

spec = importlib.util.spec_from_file_location("schema_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

ADD_COLUMN = '''
revision = "x"
def upgrade():
    op.add_column("offer", sa.Column("held_at", sa.DateTime(), nullable=True))
    op.create_index("ix", "offer", ["held_at"])
def downgrade():
    op.drop_column("offer", "held_at")
'''

DIAGRAM = (ROOT / "skills/db-diff/diagram.md").read_text().split("```")[1]


class ReadingMigrations(unittest.TestCase):
    def test_op_calls(self):
        self.assertEqual(guard.migration_changes(ADD_COLUMN), {"add column offer.held_at"})
        text = '''
TABLE = "load_holds"
def upgrade():
    op.create_table(TABLE, sa.Column("id", sa.UUID()), sa.Column("load_id", sa.UUID()))
    op.drop_table("old_thing")
    op.rename_table("held_offers", "offer_holds")
    op.alter_column("offer", "note", new_column_name="notes")
    op.alter_column("offer", "status", nullable=False)
    op.drop_column("offer", "legacy")
'''
        self.assertEqual(guard.migration_changes(text), {
            "add table load_holds", "drop table old_thing", "drop table held_offers", "add table offer_holds",
            "drop column offer.note", "add column offer.notes", "drop column offer.legacy",
        })

    def test_raw_sql(self):
        text = '''
def upgrade():
    op.execute("ALTER TABLE loads ADD COLUMN IF NOT EXISTS posted_from text, ADD CONSTRAINT ck CHECK (x > 0)")
    op.execute(sa.text("CREATE TABLE IF NOT EXISTS public.notes (id uuid, body text)"))
    op.execute("ALTER TABLE offer ALTER COLUMN status DROP NOT NULL")
    op.execute("ALTER TABLE offer DROP CONSTRAINT ck_offer")
    op.execute("ALTER TYPE offerstatus ADD VALUE IF NOT EXISTS 'held'")
    op.execute("CREATE INDEX ix ON loads (posted_from)")
    op.execute("ALTER TABLE offer RENAME COLUMN a TO b")
'''
        self.assertEqual(guard.migration_changes(text), {
            "add column loads.posted_from", "add table notes", "drop column offer.a", "add column offer.b",
        })

    def test_downgrade_and_indexes_need_nothing(self):
        text = '''
def upgrade():
    op.create_index("ix", "offer", ["x"])
    op.execute("ALTER TYPE t ADD VALUE 'v'")
def downgrade():
    op.drop_table("offer")
'''
        self.assertEqual(guard.migration_changes(text), set())

    def test_text_that_does_not_parse(self):
        self.assertEqual(guard.migration_changes('def upgrade():\n    op.add_column("a", sa.Column("b"'),
                         {"add column a.b"})
        self.assertEqual(guard.regex_changes('op.add_column(\n  "a",\n  sa.Column("b", sa.Text()))'),
                         {"add column a.b"})

    def test_every_real_migration_parses(self):
        versions = Path.home() / "dev/envoyai-backend/alembic/versions"
        if not versions.is_dir():
            self.skipTest("no backend checkout")
        for path in versions.glob("*.py"):
            guard.migration_changes(path.read_text())


class Diagram(unittest.TestCase):
    def test_the_documented_example(self):
        branch, repo, changes = guard.parse_diagram(DIAGRAM)
        self.assertEqual((branch, repo), ("aakif/eng-1234-held-offers", "envoyai-backend"))
        self.assertEqual(set(changes), {
            "add column offer.held_at", "add column offer.accepted_by_user_id", "drop column offer.legacy_note",
            "add table offer_notices", "drop table unselected_carrier_notices",
            "add table offer_holds", "drop table held_offers", "add column offer_holds.reason",
        })


class Repo(unittest.TestCase):
    """A throwaway envoyai-backend checkout on a feature branch, with origin/development behind it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.repo, state = root / "envoyai-backend", root / "state"
        patcher = mock.patch.multiple(guard, STATE=state, PENDING=state / "pending.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo.mkdir()
        self.git("init", "-q", "-b", "development")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("remote", "add", "origin", "https://github.com/envoy-ai/envoyai-backend.git")
        (self.repo / "alembic/versions").mkdir(parents=True)
        (self.repo / "alembic/versions/base.py").write_text('def upgrade():\n    op.create_table("loads")\n')
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.git("update-ref", "refs/remotes/origin/development", "HEAD")
        self.git("checkout", "-qb", "aakif/eng-1-thing")
        self.migration = self.repo / "alembic/versions/new.py"

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def pre(self, event):
        with mock.patch("sys.stdout") as out:
            guard.pre_tool_use(event)
        printed = "".join(c.args[0] for c in out.write.call_args_list)
        return json.loads(printed)["hookSpecificOutput"]["permissionDecisionReason"] if printed else None

    def test_write_is_refused_until_approved(self):
        event = {"tool_name": "Write", "tool_input": {"file_path": str(self.migration), "content": ADD_COLUMN}}
        reason = self.pre(event)
        self.assertIn("add column offer.held_at", reason)
        self.assertIn("aakif/eng-1-thing", reason)
        pending = json.loads(guard.PENDING.read_text())
        self.assertEqual(pending["changes"], ["add column offer.held_at"])
        guard.record("envoyai-backend", "aakif/eng-1-thing", ["add column offer.held_at"], "test", None)
        self.assertIsNone(self.pre(event))

    def test_edit_counts_only_what_it_adds(self):
        self.migration.write_text(ADD_COLUMN)
        same = {"tool_name": "Edit", "tool_input": {"file_path": str(self.migration),
                                                     "old_string": 'revision = "x"', "new_string": 'revision = "y"'}}
        self.assertIsNone(self.pre(same))
        more = {"tool_name": "Edit", "tool_input": {
            "file_path": str(self.migration),
            "old_string": '    op.create_index("ix", "offer", ["held_at"])',
            "new_string": '    op.add_column("offer", sa.Column("note", sa.Text()))'}}
        self.assertIn("add column offer.note", self.pre(more))

    def test_commit_reads_the_whole_worktree(self):
        self.migration.write_text(ADD_COLUMN)  # untracked: `git add x && git commit` stages it in one go
        reason = self.pre({"tool_name": "Bash", "cwd": "/",
                           "tool_input": {"command": f"cd {self.repo} && git add . && git commit -m wip"}})
        self.assertIn("add column offer.held_at", reason)
        self.assertIsNone(self.pre({"tool_name": "Bash", "cwd": "/",
                                    "tool_input": {"command": f"git -C {self.repo} push origin HEAD"}}))
        self.git("add", ".")
        self.git("commit", "-qm", "wip")
        self.assertIn("add column offer.held_at", self.pre({"tool_name": "Bash", "cwd": str(self.repo),
                                                             "tool_input": {"command": "git push"}}))

    def test_codex_patch(self):
        patch = ("*** Begin Patch\n*** Add File: alembic/versions/new.py\n"
                 + "".join(f"+{line}\n" for line in ADD_COLUMN.splitlines()) + "*** End Patch\n")
        reason = self.pre({"tool_name": "apply_patch", "cwd": str(self.repo), "tool_input": {"input": patch}})
        self.assertIn("add column offer.held_at", reason)

    def test_other_repos_and_commands_pass(self):
        self.git("remote", "set-url", "origin", "https://github.com/envoy-ai/envoyai-web.git")
        self.migration.write_text(ADD_COLUMN)
        self.assertIsNone(self.pre({"tool_name": "Bash", "cwd": str(self.repo),
                                    "tool_input": {"command": "git commit -am wip"}}))
        self.assertIsNone(self.pre({"tool_name": "Bash", "cwd": "/", "tool_input": {"command": "ls -la"}}))

    def test_a_column_on_an_approved_new_table_is_covered(self):
        guard.record("envoyai-backend", "b", ["add table notes"], "test", None)
        self.assertEqual(guard.unapproved({"add column notes.body", "add column offer.x"}, "envoyai-backend", "b"),
                         ["add column offer.x"])

    def test_agents_cannot_write_approvals(self):
        target = guard.STATE / "envoyai-backend/x.json"
        self.assertIsNotNone(self.pre({"tool_name": "Write", "tool_input": {"file_path": str(target), "content": "{}"}}))
        self.assertTrue(guard.writes_approvals("echo '{}' > ~/.agents/state/schema-approvals/envoyai-backend/x.json"))
        self.assertTrue(guard.writes_approvals("python3 -c 'open(\"schema-approvals/x\",\"w\")'"))
        self.assertFalse(guard.writes_approvals("cat ~/.agents/state/schema-approvals/envoyai-backend/x.json"))
        self.assertFalse(guard.writes_approvals("ls ~/.agents/state/schema-approvals"))

    def test_his_answer_is_recorded_from_the_question(self):
        question = "Approve the schema changes on aakif/eng-1-thing?"
        event = {"session_id": "s", "tool_input": {
            "questions": [{"question": question, "header": "Schema", "options": [
                {"label": "Approve", "description": "", "preview": DIAGRAM.replace(
                    "aakif/eng-1234-held-offers", "aakif/eng-1-thing")},
                {"label": "Don't add", "description": ""}]}],
            "answers": {question: "Approve"}}}
        with mock.patch("sys.stdout"):
            guard.record_question(event)
        self.assertIn("add column offer.held_at", guard.approved("envoyai-backend", "aakif/eng-1-thing"))

        event["tool_input"]["answers"] = {question: "Don't add"}
        guard.approval_file("envoyai-backend", "aakif/eng-1-thing").unlink()
        with mock.patch("sys.stdout"):
            guard.record_question(event)
        self.assertEqual(guard.approved("envoyai-backend", "aakif/eng-1-thing"), set())

    def test_answer_read_from_the_tool_result_text(self):
        question = "Approve the schema changes?"
        event = {"tool_input": {"questions": [{"question": question, "header": "Schema", "options": [
            {"label": "Approve", "preview": "branch: b\noffer  (existing table)\n  + x   text\n"}]}]},
            "tool_response": f'Your questions have been answered: "{question}"="Approve".'}
        with mock.patch("sys.stdout"):
            guard.record_question(event)
        self.assertEqual(guard.approved("envoyai-backend", "b"), {"add column offer.x"})

    def test_typed_approval_takes_the_pending_request(self):
        self.pre({"tool_name": "Write", "tool_input": {"file_path": str(self.migration), "content": ADD_COLUMN}})
        with mock.patch("sys.stdout"):
            guard.record_prompt({"prompt": "something else"})
        self.assertEqual(guard.approved("envoyai-backend", "aakif/eng-1-thing"), set())
        with mock.patch("sys.stdout"):
            guard.record_prompt({"prompt": "approve schema"})
        self.assertEqual(guard.approved("envoyai-backend", "aakif/eng-1-thing"), {"add column offer.held_at"})
        self.assertFalse(guard.PENDING.exists())


class Wiring(unittest.TestCase):
    def test_both_clients_run_it(self):
        claude = json.loads((ROOT / "claude/settings.hooks.json").read_text())["hooks"]
        commands = {(ev, e.get("matcher"), h["command"]) for ev, es in claude.items() for e in es for h in e["hooks"]}
        g = "~/.agents/hooks/schema-guard.py"
        for want in [("PreToolUse", "Bash", g), ("PreToolUse", "Write|Edit|MultiEdit", g),
                     ("PostToolUse", "AskUserQuestion", f"{g} --record"), ("UserPromptSubmit", None, f"{g} --prompt")]:
            self.assertIn(want, commands)
        codex = json.loads((ROOT / "codex/hooks.json").read_text())["hooks"]
        codex_cmds = {h["command"] for es in codex.values() for e in es for h in e["hooks"]}
        self.assertTrue({g, f"{g} --prompt"} <= codex_cmds)


if __name__ == "__main__":
    unittest.main()
