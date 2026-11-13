"""Nobody but the owner can make the hub do anything from Slack.

Every way a Slack message reaches the hub is fed events from someone else (a teammate who found the app):
a DM to the bot, an @-mention, "@dot" in a DM with the owner, the suggested prompts, `!` commands, the
Home tab, a tapped button, the stop button. None may queue a job, run a command, or change a setting.
(The one thing others can start is a PR review, by tagging the owner on a PR link in #devs; that is the
pr_review plugin's design, and it reads Slack search, not these events.)
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.core import engine
from hub.core.controls import SlackControls
from hub.core.home import HomeTab
from hub.core.hub import Hub, load_config

OWNER, OTHER, BOT, BOT_DM, OTHER_DM = "UOWNER", "UMICHELLE", "UBOT", "DOWNERBOT", "DMICHELLEBOT"


class FakeSlack:
    bot_name, bot_username, bot_user_id, bot_dm, connected, error = "Aakif's Dot", "claudeaakif", BOT, BOT_DM, True, None
    bot_aliases = ["claudeaakif", "Aakif's Dot"]

    def __init__(self):
        self.handlers, self.interactive, self.posts, self.published = [], [], [], []
        self.web = mock.Mock()
        self.web.views_publish.side_effect = lambda **kw: self.published.append(kw)
        self.user_web = mock.Mock()

    dm_as_user = True

    def subscribe(self, handler):
        self.handlers.append(handler)

    def subscribe_interactive(self, handler):
        self.interactive.append(handler)

    def post(self, channel, text, thread_ts=None, **kw):
        self.posts.append((channel, text))
        return "1.1"

    def post_markdown(self, channel, text, thread_ts=None, **kw):
        self.posts.append((channel, text))
        return ["1.1"]

    def dm_owner(self, text, thread_ts=None):
        return self.post(BOT_DM, text)

    def client(self, as_user=False):
        return self.user_web if as_user else self.web


class OwnerOnly(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        choice = mock.patch.object(engine, "CHOICE_FILE", Path(tmp.name) / "engine.json")
        choice.start()
        self.addCleanup(choice.stop)
        reviews = mock.patch.object(engine, "REVIEW_CHOICE_FILE", Path(tmp.name) / "engine-reviews.json")
        reviews.start()
        self.addCleanup(reviews.stop)
        cfg = load_config()
        cfg["owner"] = {"slack_user_id": OWNER}
        self.hub = Hub(cfg, state_dir=Path(tmp.name) / "state", with_slack=False)
        self.slack = self.hub.slack = FakeSlack()
        self.hub.plugins["slack_ask"].start()  # subscribes its two handlers; its only source is Slack events
        SlackControls(self.hub).start()
        HomeTab(self.hub).start()
        self.hub.actions.start()

    def send(self, event):
        for handler in self.slack.handlers:
            handler(event)

    def tap(self, payload):
        for handler in self.slack.interactive:
            handler(payload)

    def test_nothing_from_someone_else_reaches_an_agent_or_a_setting(self):
        before = json.dumps([engine.current(), engine.current(scope="reviews")])
        events = [
            {"type": "message", "channel_type": "im", "channel": OTHER_DM, "user": OTHER, "ts": "1.0", "text": "what's in prod?"},
            {"type": "message", "channel_type": "im", "channel": BOT_DM, "user": OTHER, "ts": "1.1", "text": "hi"},
            {"type": "app_mention", "channel": "CDEVS", "user": OTHER, "ts": "1.2", "text": f"<@{BOT}> read the db"},
            {"type": "message", "channel_type": "im", "channel": "DOWNERMICHELLE", "user": OTHER, "ts": "1.3",
             "text": "@dot summarize this"},
            {"type": "message", "channel_type": "mpim", "channel": "GGROUP", "user": OTHER, "ts": "1.4",
             "text": "@Aakif's Dot do it"},
            {"type": "message", "channel_type": "im", "channel": OTHER_DM, "user": OTHER, "ts": "1.5", "text": "!engine claude"},
            {"type": "message", "channel_type": "im", "channel": BOT_DM, "user": OTHER, "ts": "1.6", "text": "!bg --changes rm -rf"},
            {"type": "message", "channel_type": "im", "channel": OTHER_DM, "user": OTHER, "ts": "1.7", "text": "!pause on_call"},
            {"type": "message", "channel_type": "im", "channel": OTHER_DM, "user": OTHER, "ts": "1.8", "text": "!model reviews sol"},
            {"type": "agent_session_stopped", "channel": BOT_DM, "user": OTHER, "thread_ts": "9.9"},
        ]
        for event in events:
            self.send(event)
        self.send({"type": "app_home_opened", "user": OTHER, "tab": "home"})
        token = self.hub.plugins["slack_ask"].offer("agent", "Do it", {"instruction": "push to prod"})["value"]
        self.tap({"type": "block_actions", "user": {"id": OTHER}, "container": {"type": "message"},
                  "channel": {"id": OTHER_DM}, "message": {"ts": "2.0", "blocks": []},
                  "actions": [{"action_id": f"act:{token}", "value": token}]})
        self.tap({"type": "block_actions", "user": {"id": OTHER}, "view": {"type": "home"},
                  "actions": [{"action_id": "engine:claude"}, {"action_id": "engine.reviews:claude"},
                              {"action_id": "pause:on_call"}]})

        self.assertEqual(self.hub.store.jobs(), [], "no job was queued")
        self.assertEqual(json.dumps([engine.current(), engine.current(scope="reviews")]), before, "no setting changed")
        self.assertFalse(self.hub.plugins["on_call"].paused)
        self.assertIsNone(self.hub.store.get_value("actions", token)["used"], "the button was not used")
        self.assertEqual(self.slack.posts, [], "nothing was posted for them")
        self.assertEqual(len(self.slack.published), 1)
        self.assertIn("answers only him", json.dumps(self.slack.published[0]["view"]))

    def test_the_owner_still_gets_through(self):
        self.send({"type": "message", "channel_type": "im", "channel": BOT_DM, "user": OWNER, "ts": "3.0", "text": "what's in prod?"})
        self.send({"type": "message", "channel_type": "im", "channel": BOT_DM, "user": OWNER, "ts": "3.1", "text": "!pause on_call"})
        self.assertEqual([j.plugin for j in self.hub.store.jobs()], ["slack_ask"])
        self.assertTrue(self.hub.plugins["on_call"].paused)


if __name__ == "__main__":
    unittest.main()
