"""Unit tests for the TrueNAS app-update notification buttons."""
import ast
import pathlib
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "bot.py"
CODE = SOURCE.read_text(encoding="utf-8")
TREE = ast.parse(CODE)


class Button:
    def __init__(self, text, callback_data):
        self.text = text
        self.callback_data = callback_data


class Markup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


def load_functions(allow_updates=True):
    names = {"is_app_update_available_alert", "alert_button"}
    methods = [
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert {node.name for node in methods} == names
    env = {
        "InlineKeyboardButton": Button,
        "InlineKeyboardMarkup": Markup,
        "ALLOW_APP_UPDATES": allow_updates,
    }
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(SOURCE), "exec"), env)
    return env


class AppUpdateAlertTests(unittest.TestCase):
    def test_app_update_alert_detected(self):
        f = load_functions()["is_app_update_available_alert"]
        self.assertTrue(f({"text": "Updates are available for Jellyfin and Radarr"}))
        self.assertFalse(f({"text": "A TrueNAS system update is available"}))

    def test_alert_has_both_actions(self):
        markup = load_functions()["alert_button"]("some-uuid", True)
        self.assertEqual(
            [button.callback_data for button in markup.inline_keyboard[0]],
            ["alert:dismiss:some-uuid", "alert:updates"]
        )

    def test_unrelated_alert_only_acknowledges(self):
        markup = load_functions()["alert_button"]("some-uuid", False)
        self.assertEqual(len(markup.inline_keyboard[0]), 1)

    def test_missing_uuid_still_offers_updates(self):
        markup = load_functions()["alert_button"]("", True)
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "alert:updates")

    def test_disabled_updates_hide_action(self):
        markup = load_functions(False)["alert_button"]("some-uuid", True)
        self.assertEqual(len(markup.inline_keyboard[0]), 1)

    def test_callback_keeps_confirmation_flow(self):
        self.assertIn('if data == "alert:updates":', CODE)
        self.assertIn("await show_update_options(query.message, context.application)", CODE)
        self.assertIn('CallbackQueryHandler(callback_alert, pattern=r"^alert:(dismiss:|updates$)")', CODE)
        self.assertIn('callback_data="updates:confirm_all"', CODE)
        self.assertIn('callback_data="updates:confirm_one"', CODE)
        self.assertIn('application.bot_data.setdefault("update_flows"', CODE)
        self.assertIn('flow_key = (query.message.chat_id, query.message.message_id)', CODE)


if __name__ == "__main__":
    unittest.main()
