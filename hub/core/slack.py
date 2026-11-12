"""The hub's one Slack connection: the bot's Socket Mode link, its Web API client, and the owner's user token.

Every plugin shares it. A plugin that reacts to Slack events subscribes with
``hub.slack.subscribe(handler)``; handlers get each Events API event and must
return quickly (enqueue a job, never run an agent). The bot's own name is read
from Slack when the hub starts, so renaming the app needs no code change.

Tokens live in the macOS Keychain under the service names in config.json's
"slack.keychain" (generic passwords for this user); nothing is stored here.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from typing import Any, Callable

log = logging.getLogger("hub.slack")
Handler = Callable[[dict[str, Any]], None]


def keychain(service: str) -> str | None:
    result = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                            capture_output=True, text=True)
    return (result.stdout.strip() or None) if result.returncode == 0 else None


class SlackService:
    def __init__(self, cfg: dict[str, Any], owner: str) -> None:
        self.cfg = cfg
        self.owner = owner
        self.web: Any = None        # bot WebClient
        self.user_web: Any = None   # owner's WebClient (answers in DMs the bot cannot join), optional
        self.socket: Any = None
        self.bot_user_id: str | None = None
        self.bot_name: str | None = None   # the app's current name in Slack, e.g. "claude-aakif"
        self.bot_dm: str | None = None     # the bot's DM with the owner
        self.bot_aliases: list[str] = []   # every name the bot goes by in Slack (username, display name, real name)
        self.handlers: list[Handler] = []
        self.interactive_handlers: list[Handler] = []  # Home tab buttons and menus (block_actions payloads)
        self.error: str | None = None
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ setup

    def token(self, kind: str) -> str | None:
        service = self.cfg.get("keychain", {}).get(kind)
        return keychain(service) if service else None

    def connect_web(self) -> bool:
        """The Web API clients only (no events): enough to post. False when the bot token is missing."""
        from slack_sdk import WebClient

        bot_token = self.token("bot")
        if not bot_token:
            self.error = "bot token missing from the Keychain"
            return False
        self.web = WebClient(token=bot_token)
        auth = self.web.auth_test()
        self.bot_user_id, self.bot_name = auth["user_id"], auth.get("user")
        self.bot_aliases = [name for name in [self.bot_name] if name]
        try:  # the display and real names people type ("@claude-aakif"), beside the username ("claudeaakif")
            profile = self.web.users_info(user=self.bot_user_id)["user"]
            for name in (profile.get("profile", {}).get("display_name"), profile.get("real_name"), profile.get("name")):
                if name and name not in self.bot_aliases:
                    self.bot_aliases.append(name)
        except Exception:  # noqa: BLE001 - needs users:read; the username alone still works
            log.warning("could not read the bot's display name", exc_info=True)
        try:
            self.bot_dm = self.web.conversations_open(users=self.owner)["channel"]["id"]
        except Exception:  # noqa: BLE001 - needs im:write
            log.warning("could not open the bot's DM with the owner", exc_info=True)
        user_token = self.token("user")
        if user_token:
            user_web = WebClient(token=user_token)
            try:
                if user_web.auth_test().get("user_id") == self.owner:
                    self.user_web = user_web
                else:
                    log.error("the Slack user token does not belong to the owner; answers in other DMs stay off")
            except Exception:  # noqa: BLE001
                log.warning("the Slack user token failed to sign in", exc_info=True)
        return True

    def start(self) -> bool:
        """Connect the Web API and the event socket. False (with self.error) when tokens are missing."""
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.response import SocketModeResponse

        if not self.connect_web():
            return False
        app_token = self.token("app")
        if not app_token:
            self.error = "app-level token missing from the Keychain"
            return False

        def on_request(client: Any, req: Any) -> None:
            client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
            if req.type == "interactive":  # a button or menu on the app's Home tab
                for handler in list(self.interactive_handlers):
                    try:
                        handler(req.payload)
                    except Exception:  # noqa: BLE001
                        log.exception("a Slack interaction handler failed")
                return
            if req.type != "events_api":
                return
            event = req.payload.get("event", {})
            for handler in list(self.handlers):
                try:
                    handler(event)
                except Exception:  # noqa: BLE001 - one plugin's bug must not drop the event for the others
                    log.exception("a Slack event handler failed")

        self.socket = SocketModeClient(app_token=app_token, web_client=self.web)
        self.socket.socket_mode_request_listeners.append(on_request)
        self.socket.connect()
        self.error = None
        log.info("Slack connected as %s (%s)", self.bot_name, self.bot_user_id)
        return True

    def subscribe(self, handler: Handler) -> None:
        self.handlers.append(handler)

    def subscribe_interactive(self, handler: Handler) -> None:
        self.interactive_handlers.append(handler)

    @property
    def connected(self) -> bool:
        return bool(self.socket and self.socket.is_connected())

    @property
    def dm_as_user(self) -> bool:
        return self.user_web is not None and self.bot_dm is not None

    # ------------------------------------------------------------------ helpers

    def client(self, as_user: bool = False) -> Any:
        return self.user_web if as_user and self.user_web else self.web

    def post(self, channel: str, text: str, thread_ts: str | None = None, *, as_user: bool = False,
             broadcast: bool = False) -> str | None:
        """Post one message; returns its ts. Long text is the caller's to split."""
        kwargs: dict[str, Any] = {"channel": channel, "text": text, "unfurl_links": False, "unfurl_media": False}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
            if broadcast:
                kwargs["reply_broadcast"] = True
        response = self.client(as_user).chat_postMessage(**kwargs)
        return response.get("ts")

    def permalink(self, channel: str, ts: str, *, as_user: bool = False) -> str | None:
        try:
            return self.client(as_user).chat_getPermalink(channel=channel, message_ts=ts).get("permalink")
        except Exception:  # noqa: BLE001
            return None

    def react(self, channel: str, ts: str, name: str, *, remove: bool = False, as_user: bool = False) -> None:
        try:
            method = self.client(as_user).reactions_remove if remove else self.client(as_user).reactions_add
            method(channel=channel, timestamp=ts, name=name)
        except Exception:  # noqa: BLE001 - a missing reaction is cosmetic
            pass

    def dm_owner(self, text: str, thread_ts: str | None = None) -> str | None:
        """Post in the bot's DM with the owner."""
        if not self.bot_dm:
            return None
        return self.post(self.bot_dm, text, thread_ts)
