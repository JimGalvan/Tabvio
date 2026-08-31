"""Sign-in, callback, and sign-out routes."""

import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from tabvio.auth.models import CurrentUserResponse
from tabvio.auth.sessions import (
    CurrentUser,
    build_authorization_url,
    build_logout_url,
    clear_session_cookie,
    complete_sign_in,
    is_local_request,
    set_session_cookie,
)
from tabvio.config import SESSION_COOKIE_NAME

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/login", include_in_schema=False)
async def start_sign_in(request: Request) -> RedirectResponse:
    return RedirectResponse(
        build_authorization_url(request),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/callback", include_in_schema=False)
async def complete_callback(
    request: Request,
    code: str | None = None,
) -> RedirectResponse:
    if code is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    try:
        _, sealed_session = await complete_sign_in(code)
    except Exception as exception:
        logger.exception("Could not complete the AuthKit sign-in")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sign-in could not be completed. Try again.",
        ) from exception

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(
        response,
        sealed_session,
        secure=not is_local_request(request),
    )
    return response


@router.get("/logout", include_in_schema=False)
async def sign_out(request: Request) -> RedirectResponse:
    sealed_session = request.cookies.get(SESSION_COOKIE_NAME)
    destination = "/login"

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
