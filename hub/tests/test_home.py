import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.core import home
from hub.core.hub import Hub
from hub.core.store import JobResult


class FakeSlack:
    bot_name = "claudeaakif"
    bot_aliases = ["claudeaakif", "Aakif's Dot"]
    bot_user_id = "UBOT"
    connected = True
    dm_as_user = True
    error = None

    def __init__(self):
        self.published = []
        self.web = mock.Mock()
        self.web.views_publish.side_effect = lambda **kw: self.published.append(kw)


class Home(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.hub = Hub({"owner": {"slack_user_id": "UOWNER"}, "plugins": {}}, state_dir=Path(tmp.name), with_slack=False)
        self.hub.slack = FakeSlack()

    def test_view_has_controls_jobs_and_the_command_list(self):
        failed = self.hub.store.enqueue("slack_ask", "a question")
        self.hub.store.claim(set(), {"slack_ask": 1})
        self.hub.store.finish(failed.id, JobResult(status="failed", error="boom"))
        view = home.build_view(self.hub)
        text = json.dumps(view)
        self.assertEqual(view["type"], "home")
        self.assertEqual(view["blocks"][0]["text"]["text"], "Aakif's Dot")
        self.assertIn("engine:codex", text)
        self.assertIn('"action_id": "model"', text)
        self.assertIn("engine.reviews:codex", text)
        self.assertIn('"action_id": "model.reviews"', text)
        self.assertIn("PR reviews run on", text)
        self.assertIn(f"retry:{failed.id}", text)
        self.assertIn("!status", text)
        self.assertLessEqual(len(view["blocks"]), 100)

    def test_actions_map_to_control_commands(self):
        self.assertEqual(home.action_words({"action_id": "engine:claude"}), ["engine", "claude"])
        self.assertEqual(home.action_words({"action_id": "model", "selected_option": {"value": "opus"}}), ["model", "opus"])
        self.assertEqual(home.action_words({"action_id": "pause:pr_review"}), ["pause", "pr_review"])
        self.assertEqual(home.action_words({"action_id": "engine.reviews:claude"}), ["engine", "reviews", "claude"])
        self.assertEqual(home.action_words({"action_id": "model.reviews", "selected_option": {"value": "astra"}}),
                         ["model", "reviews", "astra"])
        self.assertEqual(home.action_words({"action_id": "effort.reviews", "selected_option": {"value": "xhigh"}}),
                         ["effort", "reviews", "xhigh"])
        self.assertIsNone(home.action_words({"action_id": "refresh"}))

    def test_only_the_owner_gets_the_controls(self):
        tab = home.HomeTab(self.hub)
        tab.on_event({"type": "app_home_opened", "user": "USOMEONE", "tab": "home"})
        self.assertIn("answers only him", json.dumps(self.hub.slack.published[-1]["view"]))
        with mock.patch.object(self.hub, "command") as command:
            tab.on_action({"type": "block_actions", "user": {"id": "USOMEONE"}, "view": {"type": "home"},
                           "actions": [{"action_id": "engine:claude"}]})
            command.assert_not_called()
            tab.on_action({"type": "block_actions", "user": {"id": "UOWNER"}, "view": {"type": "home"},
                           "actions": [{"action_id": "pause:slack_ask"}]})
            command.assert_called_once_with(["pause", "slack_ask"])
        self.assertIn("engine:codex", json.dumps(self.hub.slack.published[-1]["view"]))


if __name__ == "__main__":
    unittest.main()
