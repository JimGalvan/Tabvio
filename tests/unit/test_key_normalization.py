import unittest

from tabvio.browser.browser_utils import normalize_key


class KeyNormalizationTests(unittest.TestCase):
    def test_modifier_aliases_become_playwright_names(self) -> None:
        self.assertEqual(normalize_key("ctrl+a"), "Control+a")
        self.assertEqual(normalize_key("Ctrl+A"), "Control+A")
        self.assertEqual(normalize_key("cmd+a"), "Meta+a")
        self.assertEqual(normalize_key("option+ArrowLeft"), "Alt+ArrowLeft")

    def test_named_keys_are_capitalized(self) -> None:
        self.assertEqual(normalize_key("enter"), "Enter")
        self.assertEqual(normalize_key("esc"), "Escape")
        self.assertEqual(normalize_key("pagedown"), "PageDown")
        self.assertEqual(normalize_key("down"), "ArrowDown")

    def test_keys_playwright_already_accepts_are_left_alone(self) -> None:
        self.assertEqual(normalize_key("Enter"), "Enter")
        self.assertEqual(normalize_key("Control+A"), "Control+A")
        self.assertEqual(normalize_key("a"), "a")

    def test_an_empty_key_is_returned_unchanged(self) -> None:
        self.assertEqual(normalize_key(""), "")
        self.assertEqual(normalize_key("   "), "   ")


if __name__ == "__main__":
    unittest.main()
