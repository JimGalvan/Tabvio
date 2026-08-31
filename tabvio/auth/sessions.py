"""WorkOS AuthKit sign-in and session cookie handling.

The live view reads frames from an ``<img>`` tag and events from an
``EventSource``, and neither can send an ``Authorization`` header, so sessions
travel in a cookie. WorkOS seals that cookie for us: it holds the access and
refresh tokens encrypted with ``WORKOS_COOKIE_PASSWORD``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send
from workos import WorkOSClient
from workos.session import seal_session_from_auth_response

from tabvio.auth.models import User
from tabvio.auth.repository import UserRepository
from tabvio.config import (
    DATABASE_PATH,
    SESSION_COOKIE_MAX_AGE_SECONDS,
    SESSION_COOKIE_NAME,
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


@dataclass
class SessionState:
    """A verified WorkOS session belonging to the current request."""

    workos_user_id: str
    email: str
    sealed_session: str
    refreshed: bool


LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


def is_local_request(request: Request) -> bool:
    return request.base_url.hostname in LOCAL_HOSTNAMES


def build_redirect_uri(request: Request) -> str:
    """Work out the callback URL from the request that asked to sign in.

    Everything but localhost is treated as HTTPS. Behind Railway the app still
    speaks plain HTTP, so the request scheme would otherwise read as ``http``
    and no longer match the URI registered with WorkOS.

    Deriving this from the Host header is safe because WorkOS only accepts
    redirect URIs registered in the dashboard, so a forged host fails closed
    rather than redirecting anyone anywhere.
    """
    override = read_workos_redirect_uri_override()
    if override is not None:
        return override

    base_url = request.base_url
    scheme = "http" if is_local_request(request) else "https"
    return f"{scheme}://{base_url.netloc}/callback"


def build_authorization_url(request: Request, state: str | None = None) -> str:
    return get_client().user_management.get_authorization_url(
        provider="authkit",
        redirect_uri=build_redirect_uri(request),
        state=state,
    )


async def complete_sign_in(code: str) -> tuple[User, str]:
    """Exchange an AuthKit code for a local account and a sealed session."""
    cookie_password = read_workos_cookie_password()

    authentication = await run_in_threadpool(
        lambda: get_client().user_management.authenticate_with_code(code=code)
    )
    sealed_session = seal_session_from_auth_response(
        access_token=authentication.access_token,
        refresh_token=authentication.refresh_token,
        user=authentication.user.to_dict(),
        cookie_password=cookie_password,
    )
    user = await run_in_threadpool(
        user_repository.get_or_create_user,
        authentication.user.id,
        authentication.user.email,
    )

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
            logger.info("Could not build a WorkOS logout URL", exc_info=True)
            return None

    return await run_in_threadpool(_build)


def _read_user_claim(user: dict[str, Any] | None, claim: str) -> str | None:
    if not user:
        return None

    value = user.get(claim)
    return value if isinstance(value, str) else None


def _load_session_state(sealed_session: str) -> SessionState | None:
    """Verify the sealed cookie, refreshing the tokens when they have expired."""
    cookie_password = read_workos_cookie_password()
    session = get_client().user_management.load_sealed_session(
        session_data=sealed_session,
        cookie_password=cookie_password,
    )

    authentication = session.authenticate()
    if authentication.authenticated:
        workos_user_id = _read_user_claim(authentication.user, "id")
        email = _read_user_claim(authentication.user, "email")
        if workos_user_id is None or email is None:
            return None

        return SessionState(
            workos_user_id=workos_user_id,
            email=email,
            sealed_session=sealed_session,
            refreshed=False,
        )

    refreshed = session.refresh(cookie_password=cookie_password)
    if not refreshed.authenticated:
        return None

    workos_user_id = _read_user_claim(refreshed.user, "id")
    email = _read_user_claim(refreshed.user, "email")
    if workos_user_id is None or email is None:
        return None

    return SessionState(
        workos_user_id=workos_user_id,
        email=email,
        sealed_session=refreshed.sealed_session,
        refreshed=True,
    )


def set_session_cookie(
    response: Response,
    sealed_session: str,
    secure: bool = True,
) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        sealed_session,
        max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        secure=secure,
        samesite="lax",
    )


def clear_session_cookie(response: Response, secure: bool = True) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=secure,
        samesite="lax",
    )


def _build_cookie_headers(sealed_session: str | None, secure: bool) -> list[str]:
    """Render Set-Cookie headers without an outgoing response to hand to."""
    carrier = Response()
    if sealed_session is None:
        clear_session_cookie(carrier, secure=secure)
    else:
        set_session_cookie(carrier, sealed_session, secure=secure)

    return carrier.headers.getlist("set-cookie")


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
        if scope["type"] != "http" or scope["path"].startswith("/static"):
            await self._app(scope, receive, send)
            return

        request = Request(scope)
        sealed_session = request.cookies.get(SESSION_COOKIE_NAME)
        state: SessionState | None = None

        if sealed_session:
            try:
                state = await run_in_threadpool(_load_session_state, sealed_session)
            except Exception:
                # Tampered, stale, or sealed with a retired cookie password.
                logger.warning("Discarding a session cookie that failed to verify")
                state = None

        scope.setdefault("state", {})["session"] = state

        secure = not is_local_request(request)
        cookie_headers: list[str] = []
        if sealed_session and state is None:
            cookie_headers = _build_cookie_headers(None, secure)
        elif state is not None and state.refreshed:
            cookie_headers = _build_cookie_headers(state.sealed_session, secure)

        if not cookie_headers:
            await self._app(scope, receive, send)
            return

        async def send_with_session_cookie(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for cookie_header in cookie_headers:
                    headers.append("set-cookie", cookie_header)
            await send(message)

        await self._app(scope, receive, send_with_session_cookie)


def read_session_state(request: Request) -> SessionState | None:
    return getattr(request.state, "session", None)


async def get_current_user(request: Request) -> User:
    """Resolve the signed-in account, or reject the request with a 401."""
    state = read_session_state(request)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue",
        )

    return await run_in_threadpool(
        user_repository.get_or_create_user,
        state.workos_user_id,
        state.email,
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
