import asyncio
import unittest
from unittest.mock import patch

from fastapi import Request
from workos.session import AuthenticateWithSessionCookieFailureReason as FailureReason

from tabvio.auth import sessions
from tabvio.auth.sessions import SessionState, SessionVerdict
from tests.support import build_user


def build_state(sealed: str = "sealed", refreshed: bool = False) -> SessionState:
    return SessionState(
        workos_user_id="user_01ABC",
        email="owner@example.com",
        sealed_session=sealed,
        refreshed=refreshed,
    )


async def receive() -> dict:
    return {"type": "http.request"}


def build_scope(path: str = "/api/runs", cookie: bytes = b"wos_session=stale") -> dict:
    return {
        "type": "http",
        "path": path,
        "headers": [(b"cookie", cookie), (b"host", b"tabvio.example.com")],
    }


async def respond(scope, receive, send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def set_cookie_headers(message) -> list[str]:
    return [
        value.decode()
        for header, value in message["headers"]
        if header == b"set-cookie"
    ]


async def run_middleware(scope, application=respond) -> list:
    sent = []

    async def send(message) -> None:
        sent.append(message)

    await sessions.AuthKitSessionMiddleware(application)(scope, receive, send)
    return sent


class SessionCookieTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        sessions.reset_session_cache()
        self.addCleanup(sessions.reset_session_cache)
        user = patch.object(
            sessions.user_repository, "get_or_create_user", return_value=build_user()
        )
        user.start()
        self.addCleanup(user.stop)

    async def test_a_refreshed_session_reissues_the_cookie(self) -> None:
        state = build_state(sealed="rotated", refreshed=True)
        scope = build_scope()

        with patch.object(
            sessions, "_load_session_state", return_value=(SessionVerdict.VALID, state)
        ):
            sent = await run_middleware(scope)

        self.assertEqual(scope["state"]["session"], state)
        headers = set_cookie_headers(sent[0])
        self.assertEqual(len(headers), 1)
        self.assertIn("wos_session=rotated", headers[0])
        self.assertIn("HttpOnly", headers[0])
        self.assertIn("Secure", headers[0])

    async def test_a_rejected_session_clears_the_cookie(self) -> None:
        with patch.object(
            sessions, "_load_session_state", return_value=(SessionVerdict.REJECTED, None)
        ):
            sent = await run_middleware(build_scope())

        headers = set_cookie_headers(sent[0])
        self.assertEqual(len(headers), 1)
        self.assertIn("wos_session=", headers[0])
        self.assertIn("Max-Age=0", headers[0])

    async def test_an_unreachable_workos_leaves_the_cookie_alone(self) -> None:
        """A refresh token is not replaceable, so a network blip must not destroy it."""
        with patch.object(
            sessions,
            "_load_session_state",
            return_value=(SessionVerdict.UNAVAILABLE, None),
        ):
            sent = await run_middleware(build_scope())

        self.assertEqual(set_cookie_headers(sent[0]), [])

    async def test_a_failing_check_leaves_the_cookie_alone(self) -> None:
        with patch.object(
            sessions, "_load_session_state", side_effect=RuntimeError("jwks is down")
        ):
            sent = await run_middleware(build_scope())

        self.assertEqual(set_cookie_headers(sent[0]), [])

    async def test_a_route_that_sets_the_session_cookie_wins(self) -> None:
        """Our clear header would land after the route's and delete a fresh session."""

        async def signs_in(scope, receive, send) -> None:
            await send({
                "type": "http.response.start",
                "status": 303,
                "headers": [(b"set-cookie", b"wos_session=fresh; Path=/")],
            })
            await send({"type": "http.response.body", "body": b""})

        with patch.object(
            sessions, "_load_session_state", return_value=(SessionVerdict.REJECTED, None)
        ):
            sent = await run_middleware(build_scope(path="/callback"), signs_in)

        self.assertEqual(set_cookie_headers(sent[0]), ["wos_session=fresh; Path=/"])

    async def test_static_assets_skip_session_verification(self) -> None:
        with patch.object(sessions, "_load_session_state") as load:
            await run_middleware(build_scope(path="/static/app.js"))

        load.assert_not_called()

    async def test_a_path_merely_starting_with_static_is_still_verified(self) -> None:
        with patch.object(
            sessions, "_load_session_state", return_value=(SessionVerdict.VALID, build_state())
        ) as load:
            await run_middleware(build_scope(path="/statistics"))

        load.assert_called_once()


class ConcurrentRefreshTests(unittest.IsolatedAsyncioTestCase):
    """WorkOS rotates the refresh token, so a second concurrent use is rejected."""

    def setUp(self) -> None:
        sessions.reset_session_cache()
        self.addCleanup(sessions.reset_session_cache)
        user = patch.object(
            sessions.user_repository, "get_or_create_user", return_value=build_user()
        )
        user.start()
        self.addCleanup(user.stop)

    async def test_concurrent_requests_refresh_only_once(self) -> None:
        calls = []

        def load(sealed_session):
            calls.append(sealed_session)
            if len(calls) > 1:
                return SessionVerdict.REJECTED, None
            return SessionVerdict.VALID, build_state(sealed="rotated", refreshed=True)

        with patch.object(sessions, "_load_session_state", side_effect=load):
            results = await asyncio.gather(
                *(sessions.resolve_session("stale") for _ in range(8))
            )

        self.assertEqual(len(calls), 1)
        for verdict, state in results:
            self.assertIs(verdict, SessionVerdict.VALID)
            self.assertEqual(state.sealed_session, "rotated")

    async def test_a_later_request_still_holding_the_old_cookie_is_honoured(self) -> None:
        """The browser has not applied the new cookie to requests already in flight."""
        with patch.object(
            sessions,
            "_load_session_state",
            return_value=(SessionVerdict.VALID, build_state(sealed="rotated", refreshed=True)),
        ) as load:
            await sessions.resolve_session("stale")
            verdict, state = await sessions.resolve_session("stale")

        load.assert_called_once()
        self.assertIs(verdict, SessionVerdict.VALID)
        self.assertEqual(state.sealed_session, "rotated")

    async def test_no_response_in_a_refresh_race_clears_the_cookie(self) -> None:
        calls = []

        def load(sealed_session):
            calls.append(sealed_session)
            if len(calls) > 1:
                return SessionVerdict.REJECTED, None
            return SessionVerdict.VALID, build_state(sealed="rotated", refreshed=True)

        with patch.object(sessions, "_load_session_state", side_effect=load):
            responses = await asyncio.gather(
                *(run_middleware(build_scope()) for _ in range(6))
            )

        for sent in responses:
            for header in set_cookie_headers(sent[0]):
                self.assertIn("wos_session=rotated", header)


class RejectionReasonTests(unittest.TestCase):
    def test_a_denied_refresh_rejects_the_session(self) -> None:
        self.assertIn(FailureReason.REFRESH_DENIED, sessions._REJECTING_REASONS)

    def test_a_network_error_does_not_reject_the_session(self) -> None:
        self.assertNotIn(FailureReason.REFRESH_NETWORK_ERROR, sessions._REJECTING_REASONS)


class UserCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        sessions.reset_session_cache()
        self.addCleanup(sessions.reset_session_cache)

    async def test_the_account_is_read_once_across_many_requests(self) -> None:
        user = build_user()
        state = build_state()
        state.workos_user_id = user.workos_user_id
        state.email = user.email

        with patch.object(
            sessions.user_repository, "get_or_create_user", return_value=user
        ) as get_or_create:
            for _ in range(20):
                self.assertEqual((await sessions.resolve_user(state)).id, user.id)

        get_or_create.assert_called_once()

    async def test_a_changed_address_is_read_through(self) -> None:
        user = build_user()
        state = build_state()
        state.workos_user_id = user.workos_user_id
        state.email = user.email

        with patch.object(
            sessions.user_repository, "get_or_create_user", return_value=user
        ) as get_or_create:
            await sessions.resolve_user(state)
            state.email = "moved@example.com"
            await sessions.resolve_user(state)

        self.assertEqual(get_or_create.call_count, 2)


class DerivedOriginTests(unittest.TestCase):
    def setUp(self) -> None:
        """Ignore any redirect URI configured in the developer's own .env."""
        override = patch.object(
            sessions, "read_workos_redirect_uri_override", return_value=None
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
            sessions.build_redirect_uri(request), "http://localhost:8000/callback"
        )

    def test_deployed_callback_is_forced_to_https(self) -> None:
        """Railway terminates TLS, so the request itself still looks like http."""
        request = self.build_request("tabvio.example.com")

        self.assertFalse(sessions.is_local_request(request))
        self.assertEqual(
            sessions.build_redirect_uri(request), "https://tabvio.example.com/callback"
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
