import importlib.util
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
import json

ROOT = Path(__file__).resolve().parent.parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


config = module("codex_apply", ROOT / "codex/apply.py")
hooks = module("reminder", ROOT / "hooks/reminder.py")


class CodexConfigTests(unittest.TestCase):
    def test_fresh_config_and_second_apply(self):
        merged = config.merge("")
        self.assertEqual(tomllib.loads(merged), {
            "project_doc_fallback_filenames": ["CLAUDE.md"],
            "project_doc_max_bytes": 131072,
        })
        self.assertEqual(config.merge(merged), merged)

    def test_existing_feature_table_and_credentials_preserved(self):
        original = '''# Personal config
model = "example-model"
[features]
skills = true
js_repl = false # unrelated setting
[mcp_servers.example]
url = "https://example.com/mcp"
[mcp_servers.example.http_headers]
Authorization = "Bearer test-placeholder"
[hooks.state]
example = "preserve"
'''
        merged = config.merge(original)
        parsed = tomllib.loads(merged)
        self.assertEqual(parsed["features"], {"js_repl": False})
        for key in ("model", "mcp_servers", "hooks"):
            self.assertEqual(parsed[key], tomllib.loads(original)[key])
        self.assertIn('js_repl = false # unrelated setting', merged)
        self.assertIn('Authorization = "Bearer test-placeholder"', merged)
        self.assertEqual(config.merge(merged), merged)

    def test_multiline_fallbacks_and_explicit_budget_preserved(self):
        original = '''project_doc_fallback_filenames = [
  "TEAM.md",
]
project_doc_max_bytes = 65536
[profiles.small]
project_doc_fallback_filenames = ["OTHER.md"]
'''
        parsed = tomllib.loads(config.merge(original))
        self.assertEqual(parsed["project_doc_fallback_filenames"], ["TEAM.md", "CLAUDE.md"])
        self.assertEqual(parsed["project_doc_max_bytes"], 65536)
        self.assertEqual(parsed["profiles"]["small"]["project_doc_fallback_filenames"], ["OTHER.md"])

    def test_existing_supported_settings_are_unchanged(self):
        original = '''project_doc_fallback_filenames = ["CLAUDE.md"]
project_doc_max_bytes = 262144
[features]
hooks = true
'''
        self.assertEqual(config.merge(original), original)

    def test_invalid_config_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            original = "[features]\nskills = true\n[features]\nhooks = true\n"
            path.write_text(original)
            result = subprocess.run(
                ["python3", str(ROOT / "codex/apply.py"), "--config", str(path)],
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_text(), original)


class HookTests(unittest.TestCase):
    def test_wrappers_emit_current_sections_from_any_directory(self):
        for script, heading in [
            ("brainrot.sh", "Voice (default on)"),
            ("style-reminder.sh", "Communication style"),
        ]:
            with self.subTest(script=script):
                result = subprocess.run(
                    ["bash", str(ROOT / "hooks" / script)],
                    cwd="/", check=True, text=True, capture_output=True,
                )
                payload = json.loads(result.stdout)["hookSpecificOutput"]
                self.assertEqual(payload["hookEventName"], "UserPromptSubmit")
                section = (ROOT / "AGENTS.md").read_text().split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0].strip()
                self.assertIn(section, payload["additionalContext"])

    def test_toggle_overrides_prior_voice_and_restores_current_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AGENTS.md").write_text("## Voice (default on)\nFirst rule.\n")
            flag = root / ".brainrot-off"
            flag.touch()
            off = hooks.reminder("Voice (default on)", root)
            self.assertIn("brainrot is off", off["hookSpecificOutput"]["additionalContext"])
            (root / "AGENTS.md").write_text("## Voice (default on)\nUpdated rule.\n")
            flag.unlink()
            on = hooks.reminder("Voice (default on)", root)
            self.assertIn("Updated rule.", on["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()

