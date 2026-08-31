import unittest

from tabvio.auth.constants import SIGNED_IN_PATH
from tabvio.auth.sessions import (
    create_login_state,
    is_safe_return_path,
    read_return_path,
)


class LoginStateTests(unittest.TestCase):
    def test_state_round_trips_the_return_path(self) -> None:
        state, token = create_login_state("/app")

        self.assertEqual(read_return_path(state, token), "/app")

    def test_callback_without_a_matching_token_is_refused(self) -> None:
        state, _ = create_login_state("/app")

        self.assertIsNone(read_return_path(state, "some-other-token"))

    def test_callback_without_any_token_is_refused(self) -> None:
        """A browser that never visited /login has no state cookie."""
        state, _ = create_login_state("/app")

        self.assertIsNone(read_return_path(state, None))

    def test_missing_state_is_refused(self) -> None:
        self.assertIsNone(read_return_path(None, "token"))

    def test_malformed_state_is_refused(self) -> None:
        self.assertIsNone(read_return_path("no-separator-here", "token"))

    def test_offsite_return_paths_are_replaced(self) -> None:
        for hostile_path in ("https://evil.example.com", "//evil.example.com"):
            with self.subTest(path=hostile_path):
                state, token = create_login_state(hostile_path)

                self.assertEqual(read_return_path(state, token), SIGNED_IN_PATH)

    def test_offsite_paths_are_rejected_outright(self) -> None:
        self.assertTrue(is_safe_return_path("/app"))
        self.assertFalse(is_safe_return_path("//evil.example.com"))
        self.assertFalse(is_safe_return_path("https://evil.example.com"))
        self.assertFalse(is_safe_return_path("/app\\@evil.example.com"))

    def test_each_sign_in_gets_a_fresh_token(self) -> None:
        _, first_token = create_login_state("/app")
        _, second_token = create_login_state("/app")

        self.assertNotEqual(first_token, second_token)


if __name__ == "__main__":
    unittest.main()
