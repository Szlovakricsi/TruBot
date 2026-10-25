"""Protect consistent English bot copy and shared command descriptions."""
import ast
import pathlib
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "bot.py"
CODE = SOURCE.read_text(encoding="utf-8")
TREE = ast.parse(CODE)
EXPECTED = (
    ("status", "System health"),
    ("apps", "Installed apps"),
    ("alerts", "Active alerts"),
    ("updates", "App updates"),
    ("restart", "Restart an app"),
    ("version", "Bot version"),
    ("help", "List commands"),
)


class EnglishUiTests(unittest.TestCase):
    def test_command_menu_and_help_share_descriptions(self):
        node = next(
            n for n in TREE.body if isinstance(n, ast.AnnAssign)
            and isinstance(n.target, ast.Name)
            and n.target.id == "COMMAND_DESCRIPTIONS"
        )
        self.assertEqual(ast.literal_eval(node.value), EXPECTED)
        self.assertIn("for command, description in COMMAND_DESCRIPTIONS", CODE)
        self.assertIn("set_my_commands(list(COMMAND_DESCRIPTIONS))", CODE)
        self.assertTrue(all(len(text) <= 32 for _, text in EXPECTED))

    def test_no_hungarian_bot_strings(self):
        for node in ast.walk(TREE):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                with self.subTest(text=node.value[:40]):
                    self.assertFalse(
                        any(letter in node.value for letter in "áéíóöőúüűÁÉÍÓÖŐÚÜŰ")
                    )

    def test_common_button_labels_and_durations(self):
        for text in ['"✅ Acknowledge"', '"⬆️ Update"', '"✅ Update"', '"❌ Cancel"',
                     '"🏁 Updates complete"', 'return f"{days}d {hours}h"',
                     'return f"{minutes}m {seconds}s"']:
            with self.subTest(text=text):
                self.assertIn(text, CODE)


if __name__ == "__main__":
    unittest.main()
