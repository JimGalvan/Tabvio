import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException, Request

from tabvio.auth import sessions
from tabvio.auth.models import User
from tabvio.auth.sessions import SessionState


def build_request(session: SessionState | None) -> Request:
    return Request({"type": "http", "state": {"session": session}})


def build_session_state(refreshed: bool = False) -> SessionState:
    return SessionState(
        workos_user_id="user_01ABC",
        email="owner@example.com",
        sealed_session="sealed",
        refreshed=refreshed,
    )


class CurrentUserTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_session_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised_exception:
            await sessions.get_current_user(build_request(None))

        self.assertEqual(raised_exception.exception.status_code, 401)

    async def test_session_resolves_to_the_local_account(self) -> None:
        user = User(
            id=uuid4(),
            workos_user_id="user_01ABC",
            email="owner@example.com",
        )

        with patch.object(
            sessions.user_repository,
            "get_or_create_user",
            return_value=user,
        ) as get_or_create_user:
            resolved = await sessions.get_current_user(
                build_request(build_session_state())
            )

        self.assertEqual(resolved.id, user.id)
        get_or_create_user.assert_called_once_with("user_01ABC", "owner@example.com")


class SessionCookieTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_session_is_refreshed_and_the_cookie_is_reissued(
        self,
    ) -> None:
        sent_messages = []
        state = build_session_state(refreshed=True)

        async def receive() -> dict:
            return {"type": "http.request"}

        async def send(message) -> None:
            sent_messages.append(message)

        async def application(scope, receive, send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = sessions.AuthKitSessionMiddleware(application)
        scope = {
            "type": "http",
            "path": "/api/runs",
            "headers": [
                (b"cookie", b"wos_session=stale"),
                (b"host", b"tabvio.example.com"),
            ],
        }

        with patch.object(sessions, "_load_session_state", return_value=state):
            await middleware(scope, receive, send)

        self.assertEqual(scope["state"]["session"], state)

        start_message = sent_messages[0]
        set_cookie_headers = [
            value.decode()
            for header, value in start_message["headers"]
            if header == b"set-cookie"
        ]
        self.assertEqual(len(set_cookie_headers), 1)
        self.assertIn("wos_session=sealed", set_cookie_headers[0])
        self.assertIn("HttpOnly", set_cookie_headers[0])
        self.assertIn("Secure", set_cookie_headers[0])

    async def test_static_assets_skip_session_verification(self) -> None:
        async def receive() -> dict:
            return {"type": "http.request"}

        async def send(message) -> None:
            return None

        async def application(scope, receive, send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})

        middleware = sessions.AuthKitSessionMiddleware(application)
        scope = {
            "type": "http",
            "path": "/static/app.js",
            "headers": [(b"cookie", b"wos_session=stale")],
        }

        with patch.object(sessions, "_load_session_state") as load_session_state:
            await middleware(scope, receive, send)

        load_session_state.assert_not_called()


class DerivedOriginTests(unittest.TestCase):
    def setUp(self) -> None:
        """Ignore any redirect URI configured in the developer's own .env."""
        override = patch.object(
            sessions,
            "read_workos_redirect_uri_override",
            return_value=None,
        )
        override.start()
        self.addCleanup(override.stop)

    def build_request(self, host: str, scheme: str = "http") -> Request:
        return Request(
            {
                "type": "http",
                "scheme": scheme,
                "server": (host.split(":")[0], 80),
                "path": "/login",
                "headers": [(b"host", host.encode())],
            }
        )

    def test_local_callback_stays_on_http(self) -> None:
        request = self.build_request("localhost:8000")

        self.assertTrue(sessions.is_local_request(request))
        self.assertEqual(
            sessions.build_redirect_uri(request),
            "http://localhost:8000/callback",
        )

    def test_deployed_callback_is_forced_to_https(self) -> None:
        """Railway terminates TLS, so the request itself still looks like http."""
        request = self.build_request("tabvio.example.com")

        self.assertFalse(sessions.is_local_request(request))
        self.assertEqual(
            sessions.build_redirect_uri(request),
            "https://tabvio.example.com/callback",
        )

    def test_explicit_override_wins(self) -> None:
        request = self.build_request("tabvio.example.com")

        with patch.object(
            sessions,
            "read_workos_redirect_uri_override",
            return_value="https://proxied.example.com/callback",
        ):
            self.assertEqual(
                sessions.build_redirect_uri(request),
                "https://proxied.example.com/callback",
            )


if __name__ == "__main__":
    unittest.main()
