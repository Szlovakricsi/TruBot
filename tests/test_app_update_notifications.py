"""Integration-like tests for app update notification polling and state."""
import ast
import asyncio
import pathlib
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "bot.py"
CODE = SOURCE.read_text(encoding="utf-8")
TREE = ast.parse(CODE)


def build_functions(names, **deps):
    nodes = [
        node for node in TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    assert len(nodes) == len(names)
    ns = dict(deps)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns


class FakeButton:
    def __init__(self, text, callback_data):
        self.text = text
        self.callback_data = callback_data


class FakeKeyboard:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


class FakeBot:
    def __init__(self):
        self.sent = []
        self.fail_chat = None

    async def send_message(self, **kwargs):
        if kwargs["chat_id"] == self.fail_chat:
            raise RuntimeError("Telegram unavailable")
        self.sent.append(kwargs)


class FakeApp:
    def __init__(self):
        self.bot_data = {}
        self.bot = FakeBot()


class SilentLogger:
    def exception(self, *args, **kwargs):
        pass


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.ns = build_functions(["app_update_fingerprint", "newly_available_apps"])

    def test_catalog_version_changes_identity(self):
        f = self.ns["app_update_fingerprint"]
        one = {"name": "sonarr", "upgrade_available": True, "latest_version": "2.0"}
        two = {**one, "latest_version": "2.1"}
        self.assertNotEqual(f(one), f(two))

    def test_custom_image_has_stable_identity(self):
        f = self.ns["app_update_fingerprint"]
        image = {"name": "trubot", "image_updates_available": True}
        self.assertEqual(f(image), "trubot|image")
        self.assertEqual(self.ns["newly_available_apps"]([image], {f(image)}), [])


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = {"alert_fingerprints": ["uuid:abc"], "alerts_initialized": True}
        self.apps = [
            {"name": "sonarr", "image_updates_available": True},
            {"name": "radarr", "upgrade_available": True, "latest_version": "5"},
        ]
        def load_state():
            return self.state.copy()
        def save_state(state):
            self.state = state.copy()
        def get_apps():
            return self.apps
        self.ns = build_functions(
            ["app_update_fingerprint", "newly_available_apps",
             "format_app_update_notification", "refresh_and_send_app_updates"],
            asyncio=asyncio, Application=object,
            APP_UPDATE_NOTIFICATIONS=True, ALLOWED_CHAT_IDS={101, 202},
            ALLOW_APP_UPDATES=True, time=__import__("time"),
            get_updateable_apps_sync=get_apps,
            load_state=load_state, save_state=save_state,
            update_label=lambda app: app["name"],
            InlineKeyboardButton=FakeButton, InlineKeyboardMarkup=FakeKeyboard,
            log=SilentLogger(),
        )
        self.app = FakeApp()
        self.refresh = self.ns["refresh_and_send_app_updates"]

    async def test_first_poll_notifies_both_chats_once(self):
        await self.refresh(self.app)
        self.assertEqual(len(self.app.bot.sent), 2)
        self.assertIn("sonarr", self.app.bot.sent[0]["text"])
        self.assertIn("radarr", self.app.bot.sent[0]["text"])
        button = self.app.bot.sent[0]["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.callback_data, "alert:updates")
        self.assertEqual(len(self.state["alert_fingerprints"]), 1)
        await self.refresh(self.app)
        self.assertEqual(len(self.app.bot.sent), 2)

    async def test_new_app_notifies_only_new_name(self):
        await self.refresh(self.app)
        self.apps.append({"name": "jellyfin", "image_updates_available": True})
        await self.refresh(self.app)
        self.assertEqual(len(self.app.bot.sent), 4)
        self.assertIn("jellyfin", self.app.bot.sent[-1]["text"])
        self.assertNotIn("sonarr", self.app.bot.sent[-1]["text"])

    async def test_disappearing_update_resets_notice(self):
        await self.refresh(self.app)
        self.apps = []
        await self.refresh(self.app)
        self.assertEqual(self.state["app_update_notified"]["101"], [])
        self.apps = [{"name": "sonarr", "image_updates_available": True}]
        await self.refresh(self.app)
        self.assertEqual(len(self.app.bot.sent), 4)

    async def test_failed_delivery_retries_only_failed_chat(self):
        self.app.bot.fail_chat = 202
        await self.refresh(self.app)
        self.assertEqual(len(self.app.bot.sent), 1)
        self.app.bot.fail_chat = None
        await self.refresh(self.app)
        self.assertEqual(len(self.app.bot.sent), 2)
        self.assertEqual(self.app.bot.sent[-1]["chat_id"], 202)

    async def test_restart_keeps_state(self):
        await self.refresh(self.app)
        second_app = FakeApp()
        await self.refresh(second_app)
        self.assertEqual(second_app.bot.sent, [])

    async def test_errors_preserve_previous_notification_state(self):
        await self.refresh(self.app)
        before = self.state["app_update_notified"].copy()
        def fail():
            raise RuntimeError("TrueNAS is offline")
        self.ns["get_updateable_apps_sync"] = fail
        self.apps = []
        await self.refresh(self.app)
        self.assertEqual(self.state["app_update_notified"], before)

    async def test_no_updates_does_not_generate_notice(self):
        self.apps = []
        await self.refresh(self.app)
        self.assertEqual(self.app.bot.sent, [])


class WiringTests(unittest.TestCase):
    def test_polling_is_started_and_native_duplicates_suppressed(self):
        self.assertIn("application.create_task(app_update_safety_poll(application))", CODE)
        self.assertIn("if APP_UPDATE_NOTIFICATIONS and is_app_update_available_alert(alert):", CODE)
        self.assertIn('"APP_UPDATE_POLL_SECONDS", "300"', CODE)
        self.assertIn("notification_state_lock", CODE)


if __name__ == "__main__":
    unittest.main()
