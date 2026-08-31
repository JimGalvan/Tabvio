"""WorkOS AuthKit sign-in and session cookie handling.

The live view reads frames from an ``<img>`` tag and events from an
``EventSource``, and neither can send an ``Authorization`` header, so sessions
travel in a cookie. WorkOS seals that cookie for us: it holds the access and
refresh tokens encrypted with ``WORKOS_COOKIE_PASSWORD``.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send
from workos import WorkOSClient
from workos.session import (
    AuthenticateWithSessionCookieFailureReason,
    seal_session_from_auth_response,
)

from tabvio.auth.constants import (
    LOGIN_STATE_COOKIE_MAX_AGE_SECONDS,
    LOGIN_STATE_COOKIE_NAME,
    REFRESHED_SESSION_MEMO_SECONDS,
    SESSION_CACHE_ENTRIES,
    SESSION_COOKIE_MAX_AGE_SECONDS,
    SESSION_COOKIE_NAME,
    SIGNED_IN_PATH,
    USER_CACHE_SECONDS,
)
from tabvio.auth.models import User
from tabvio.auth.repository import UserRepository
from tabvio.config import (
    DATABASE_PATH,
    read_workos_api_key,
    read_workos_client_id,
    read_workos_cookie_password,
    read_workos_redirect_uri_override,
)

logger = logging.getLogger(__name__)

user_repository = UserRepository(DATABASE_PATH)

_client: WorkOSClient | None = None


def get_client() -> WorkOSClient:
    """Build the WorkOS client on first use.

    Deferred so the application still imports without WorkOS credentials, which
    keeps the tests runnable on a bare checkout.
    """
    global _client
    if _client is None:
        _client = WorkOSClient(
            api_key=read_workos_api_key(),
            client_id=read_workos_client_id(),
        )
    return _client


def verify_auth_configuration() -> None:
    """Fail at startup rather than turning every request into a silent 401."""
    read_workos_api_key()
    read_workos_client_id()
    read_workos_cookie_password()


class SessionVerdict(StrEnum):
    VALID = "valid"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass
class SessionState:
    workos_user_id: str
    email: str
    sealed_session: str
    refreshed: bool


LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


def is_local_request(request: Request) -> bool:
    return request.base_url.hostname in LOCAL_HOSTNAMES


def build_redirect_uri(request: Request) -> str:
    """Work out the callback URL from the request that asked to sign in.

    Deriving this from the Host header is safe because WorkOS only accepts
    redirect URIs registered in the dashboard, so a forged host fails closed
    rather than redirecting anyone anywhere. Everything but localhost is
    treated as HTTPS: behind Railway the app still speaks plain HTTP, so the
    request scheme would otherwise read as ``http`` and no longer match the
    URI registered with WorkOS.
    """
    override = read_workos_redirect_uri_override()
    if override is not None:
        return override

    scheme = "http" if is_local_request(request) else "https"
    return f"{scheme}://{request.base_url.netloc}/callback"


def is_safe_return_path(path: str) -> bool:
    """Only same-site paths, so the state cannot carry an open redirect."""
    return path.startswith("/") and not path.startswith("//") and "\\" not in path


def create_login_state(return_path: str) -> tuple[str, str]:
    """Build the OAuth state and the token the callback will check it against.

    The state carries where to land after signing in, and the random token in
    front of it is what makes the callback verifiable: a callback that arrives
    without a matching cookie did not start here.
    """
    if not is_safe_return_path(return_path):
        return_path = SIGNED_IN_PATH

    token = secrets.token_urlsafe(32)
    return f"{token}:{return_path}", token


def read_return_path(state: str | None, expected_token: str | None) -> str | None:
    """Check the callback against the token this browser was issued.

    Returns the path to land on, or None when the state is missing, malformed,
    or does not match, which means the callback should be refused.
    """
    if not state or not expected_token:
        return None

    token, separator, return_path = state.partition(":")
    if not separator or not secrets.compare_digest(token, expected_token):
        return None

    return return_path if is_safe_return_path(return_path) else SIGNED_IN_PATH


def build_authorization_url(
    request: Request,
    state: str | None = None,
    screen_hint: Literal["sign-in", "sign-up"] = "sign-in",
) -> str:
    return get_client().user_management.get_authorization_url(
        provider="authkit",
        redirect_uri=build_redirect_uri(request),
        screen_hint=screen_hint,
        state=state,
    )


async def complete_sign_in(code: str) -> tuple[User, str]:
    authentication = await run_in_threadpool(
        lambda: get_client().user_management.authenticate_with_code(code=code)
    )
    sealed_session = seal_session_from_auth_response(
        access_token=authentication.access_token,
        refresh_token=authentication.refresh_token,
        user=authentication.user.to_dict(),
        cookie_password=read_workos_cookie_password(),
    )
    user = await run_in_threadpool(
        user_repository.get_or_create_user,
        authentication.user.id,
        authentication.user.email,
    )
    _remember_user(user)

    return user, sealed_session


async def build_logout_url(sealed_session: str) -> str | None:
    """The WorkOS URL that ends the session on their side, if it is still valid."""

    def _build() -> str | None:
        try:
            session = get_client().user_management.load_sealed_session(
                session_data=sealed_session,
                cookie_password=read_workos_cookie_password(),
            )
            return session.get_logout_url()
        except Exception:
            logger.warning("Could not build a WorkOS logout URL", exc_info=True)
            return None

    return await run_in_threadpool(_build)


def _read_user_claim(user: dict[str, Any] | None, claim: str) -> str | None:
    if not user:
        return None

    value = user.get(claim)
    return value if isinstance(value, str) else None


def _state_from(
    authentication: Any,
    sealed_session: str,
    refreshed: bool,
) -> SessionState | None:
    workos_user_id = _read_user_claim(authentication.user, "id")
    email = _read_user_claim(authentication.user, "email")
    if workos_user_id is None or email is None:
        logger.warning("A WorkOS session carried no usable user claims")
        return None

    return SessionState(
        workos_user_id=workos_user_id,
        email=email,
        sealed_session=sealed_session,
        refreshed=refreshed,
    )


# Only these mean WorkOS actually told us the session is finished. Anything
# else -- a network error, an unrecognised reason string -- leaves the cookie
# in place so the still-valid refresh token survives to be retried.
_REJECTING_REASONS = {
    AuthenticateWithSessionCookieFailureReason.INVALID_JWT,
    AuthenticateWithSessionCookieFailureReason.INVALID_SESSION_COOKIE,
    AuthenticateWithSessionCookieFailureReason.NO_SESSION_COOKIE_PROVIDED,
    AuthenticateWithSessionCookieFailureReason.REFRESH_DENIED,
    AuthenticateWithSessionCookieFailureReason.MFA_CHALLENGE_REQUIRED,
    AuthenticateWithSessionCookieFailureReason.MFA_ENROLLMENT_REQUIRED,
    AuthenticateWithSessionCookieFailureReason.SSO_REQUIRED,
    AuthenticateWithSessionCookieFailureReason.EMAIL_VERIFICATION_REQUIRED,
    AuthenticateWithSessionCookieFailureReason.ORGANIZATION_SELECTION_REQUIRED,
    AuthenticateWithSessionCookieFailureReason.ORGANIZATION_AUTH_METHODS_REQUIRED,
    AuthenticateWithSessionCookieFailureReason.AUTHENTICATION_METHOD_NOT_ALLOWED,
    AuthenticateWithSessionCookieFailureReason.RADAR_CHALLENGE_REQUIRED,
}


def _load_session_state(
    sealed_session: str,
) -> tuple[SessionVerdict, SessionState | None]:
    """Verify the sealed cookie, refreshing the tokens when they have expired.

    Blocking: this reaches WorkOS on refresh and may fetch JWKS. Go through
    ``resolve_session`` rather than calling it directly.
    """
    cookie_password = read_workos_cookie_password()
    session = get_client().user_management.load_sealed_session(
        session_data=sealed_session,
        cookie_password=cookie_password,
    )

    authentication = session.authenticate()
    if authentication.authenticated:
        state = _state_from(authentication, sealed_session, refreshed=False)
        return (SessionVerdict.VALID, state) if state else (SessionVerdict.REJECTED, None)

    refreshed = session.refresh(cookie_password=cookie_password)
    if not refreshed.authenticated:
        if refreshed.reason in _REJECTING_REASONS:
            return SessionVerdict.REJECTED, None

        logger.warning("Could not refresh a session: %s", refreshed.reason)
        return SessionVerdict.UNAVAILABLE, None

    state = _state_from(refreshed, refreshed.sealed_session, refreshed=True)
    return (SessionVerdict.VALID, state) if state else (SessionVerdict.REJECTED, None)


@dataclass
class _SessionGate:
    """Serialises verification of one sealed cookie value and memoises the result.

    WorkOS rotates the refresh token on every refresh, so a second concurrent
    use of the same one is rejected. The dashboard keeps an SSE stream and a
    frame poll in flight at once, so without this they race and the loser's
    response clears the cookie the winner just rotated. The memo also covers
    the requests already on the wire holding the superseded cookie.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state: SessionState | None = None
    memoised_at: float = 0.0


_session_gates: OrderedDict[str, _SessionGate] = OrderedDict()
_user_cache: OrderedDict[str, tuple[float, User]] = OrderedDict()


def reset_session_cache() -> None:
    """Drop every memoised session and account. For tests and cookie rotation."""
    _session_gates.clear()
    _user_cache.clear()


def _gate_for(sealed_session: str) -> _SessionGate:
    gate = _session_gates.get(sealed_session)
    if gate is None:
        gate = _SessionGate()
        _session_gates[sealed_session] = gate
        while len(_session_gates) > SESSION_CACHE_ENTRIES:
            _session_gates.popitem(last=False)
    else:
        _session_gates.move_to_end(sealed_session)

    return gate


def _read_memo(gate: _SessionGate) -> SessionState | None:
    if gate.state is None:
        return None

    if time.monotonic() - gate.memoised_at > REFRESHED_SESSION_MEMO_SECONDS:
        gate.state = None
        return None

    return gate.state


async def resolve_session(
    sealed_session: str,
) -> tuple[SessionVerdict, SessionState | None]:
    """Verify one sealed cookie, at most once at a time across concurrent requests."""
    gate = _gate_for(sealed_session)

    memoised = _read_memo(gate)
    if memoised is not None:
        return SessionVerdict.VALID, memoised

    async with gate.lock:
        memoised = _read_memo(gate)
        if memoised is not None:
            return SessionVerdict.VALID, memoised

        try:
            verdict, state = await run_in_threadpool(_load_session_state, sealed_session)
        except Exception:
            # A tampered or stale cookie comes back as a verdict rather than an
            # exception, so reaching here means the check itself failed. Keep
            # the cookie: it is probably still good.
            logger.warning("Could not verify a session cookie", exc_info=True)
            return SessionVerdict.UNAVAILABLE, None

        if verdict is SessionVerdict.VALID and state is not None and state.refreshed:
            gate.state = state
            gate.memoised_at = time.monotonic()

        return verdict, state


def _remember_user(user: User) -> None:
    _user_cache[user.workos_user_id] = (time.monotonic(), user)
    while len(_user_cache) > SESSION_CACHE_ENTRIES:
        _user_cache.popitem(last=False)


async def resolve_user(state: SessionState) -> User:
    """The local account for a verified session, from cache where possible.

    The dashboard polls a frame every 750ms with an SSE stream open, and the
    row never changes apart from the address, so reading it per request would
    put a database connection per frame on the threadpool for nothing.
    """
    cached = _user_cache.get(state.workos_user_id)
    if cached is not None:
        cached_at, user = cached
        if (
            time.monotonic() - cached_at <= USER_CACHE_SECONDS
            and user.email == state.email
        ):
            _user_cache.move_to_end(state.workos_user_id)
            return user

    user = await run_in_threadpool(
        user_repository.get_or_create_user,
        state.workos_user_id,
        state.email,
    )
    _remember_user(user)
    return user


def write_cookie(
    response: Response,
    name: str,
    value: str | None,
    secure: bool,
    max_age: int = 0,
) -> None:
    """Set one of our cookies, or clear it when the value is None."""
    if value is None:
        response.delete_cookie(
            name, path="/", httponly=True, secure=secure, samesite="lax"
        )
        return

    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path="/",
        httponly=True,
        secure=secure,
        samesite="lax",
    )


def set_session_cookie(response: Response, sealed_session: str, secure: bool) -> None:
    write_cookie(
        response,
        SESSION_COOKIE_NAME,
        sealed_session,
        secure,
        SESSION_COOKIE_MAX_AGE_SECONDS,
    )


def clear_session_cookie(response: Response, secure: bool) -> None:
    write_cookie(response, SESSION_COOKIE_NAME, None, secure)


def set_login_state_cookie(response: Response, token: str, secure: bool) -> None:
    write_cookie(
        response,
        LOGIN_STATE_COOKIE_NAME,
        token,
        secure,
        LOGIN_STATE_COOKIE_MAX_AGE_SECONDS,
    )


def clear_login_state_cookie(response: Response, secure: bool) -> None:
    write_cookie(response, LOGIN_STATE_COOKIE_NAME, None, secure)


def _build_cookie_headers(sealed_session: str | None, secure: bool) -> list[str]:
    """Render Set-Cookie headers without an outgoing response to hand to."""
    carrier = Response()
    if sealed_session is None:
        clear_session_cookie(carrier, secure=secure)
    else:
        set_session_cookie(carrier, sealed_session, secure=secure)

    return carrier.headers.getlist("set-cookie")


_SESSION_COOKIE_PREFIX = f"{SESSION_COOKIE_NAME}=".encode()


def _sets_session_cookie(headers: list[tuple[bytes, bytes]]) -> bool:
    return any(
        name.lower() == b"set-cookie"
        and value.lstrip().startswith(_SESSION_COOKIE_PREFIX)
        for name, value in headers
    )


class AuthKitSessionMiddleware:
    """Verify the session cookie once per request and keep it fresh.

    Written as raw ASGI rather than ``BaseHTTPMiddleware`` because the run
    stream and the MJPEG feed are long-lived streaming responses that rely on
    ``request.is_disconnected()``, which the higher-level base class interferes
    with. Refreshing here, rather than inside a dependency, means the rotated
    cookie is attached to every kind of response, streams included. A rotated
    refresh token that never reached the browser would sign the user out on
    their next request.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or path == "/static" or path.startswith("/static/"):
            await self._app(scope, receive, send)
            return

        request = Request(scope)
        sealed_session = request.cookies.get(SESSION_COOKIE_NAME)
        verdict = SessionVerdict.REJECTED
        state: SessionState | None = None

        if sealed_session:
            verdict, state = await resolve_session(sealed_session)

        request_state = scope.setdefault("state", {})
        request_state["session"] = state
        request_state["user"] = await resolve_user(state) if state is not None else None

        cookie_headers: list[str] = []
        if sealed_session and verdict is SessionVerdict.REJECTED:
            cookie_headers = _build_cookie_headers(None, not is_local_request(request))
        elif state is not None and state.refreshed:
            cookie_headers = _build_cookie_headers(
                state.sealed_session, not is_local_request(request)
            )

        if not cookie_headers:
            await self._app(scope, receive, send)
            return

        async def send_with_session_cookie(message: Any) -> None:
            # A route that sets the session cookie itself -- /callback, /logout
            # -- knows more than we do, and our header would land after its own
            # and win. Leave those responses alone.
            if message["type"] == "http.response.start" and not _sets_session_cookie(
                message["headers"]
            ):
                headers = MutableHeaders(scope=message)
                for cookie_header in cookie_headers:
                    headers.append("set-cookie", cookie_header)
            await send(message)

        await self._app(scope, receive, send_with_session_cookie)


def read_session_state(request: Request) -> SessionState | None:
    return getattr(request.state, "session", None)


async def get_current_user(request: Request) -> User:
    """Resolve the signed-in account, or reject the request with a 401."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue",
        )

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
