"""Sign-in, sign-up, callback, and sign-out routes."""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from tabvio.auth.constants import (
    LOGIN_STATE_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SIGNED_IN_PATH,
)
from tabvio.auth.models import CurrentUserResponse
from tabvio.auth.sessions import (
    CurrentUser,
    build_authorization_url,
    build_logout_url,
    clear_login_state_cookie,
    clear_session_cookie,
    complete_sign_in,
    create_login_state,
    is_local_request,
    read_return_path,
    set_login_state_cookie,
    set_session_cookie,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _start_authkit(
    request: Request,
    screen_hint: Literal["sign-in", "sign-up"],
) -> RedirectResponse:
    return_path = request.query_params.get("next", SIGNED_IN_PATH)
    state, token = create_login_state(return_path)

    response = RedirectResponse(
        build_authorization_url(request, state=state, screen_hint=screen_hint),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_login_state_cookie(response, token, secure=not is_local_request(request))
    return response


@router.get("/login", include_in_schema=False)
async def start_sign_in(request: Request) -> RedirectResponse:
    return _start_authkit(request, "sign-in")


@router.get("/signup", include_in_schema=False)
async def start_sign_up(request: Request) -> RedirectResponse:
    return _start_authkit(request, "sign-up")


@router.get("/callback", include_in_schema=False)
async def complete_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    secure = not is_local_request(request)

    if code is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    return_path = read_return_path(state, request.cookies.get(LOGIN_STATE_COOKIE_NAME))
    if return_path is None:
        # The callback did not begin at /login or /signup in this browser.
        logger.warning("Refused an AuthKit callback with unrecognised state")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This sign-in link is no longer valid. Start again.",
        )

    try:
        _, sealed_session = await complete_sign_in(code)
    except Exception as exception:
        logger.exception("Could not complete the AuthKit sign-in")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sign-in could not be completed. Try again.",
        ) from exception

    response = RedirectResponse(return_path, status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, sealed_session, secure=secure)
    clear_login_state_cookie(response, secure=secure)
    return response


@router.get("/logout", include_in_schema=False)
async def sign_out(request: Request) -> RedirectResponse:
    sealed_session = request.cookies.get(SESSION_COOKIE_NAME)
    destination = "/"

    if sealed_session:
        logout_url = await build_logout_url(sealed_session)
        if logout_url is not None:
            destination = logout_url

    response = RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, secure=not is_local_request(request))
    return response


@router.get("/api/auth/me", response_model=CurrentUserResponse)
async def get_signed_in_user(user: CurrentUser) -> CurrentUserResponse:
    return CurrentUserResponse(id=user.id, email=user.email)
