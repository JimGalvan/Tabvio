"""Application settings and filesystem paths."""

import os
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
STATIC_DIRECTORY = Path(__file__).resolve().parent / "server" / "static"
DATABASE_PATH = PROJECT_DIRECTORY / "data" / "tabvio.db"

SESSION_COOKIE_NAME = "wos_session"
SESSION_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def read_headless_setting() -> bool:
    configured_value = os.getenv("TABVIO_HEADLESS", "true").strip().lower()
    return configured_value not in {"false", "0", "no"}


def read_max_concurrent_runs(default: int) -> int:
    configured_value = os.getenv("TABVIO_MAX_CONCURRENT_RUNS", str(default))
    try:
        value = int(configured_value)
    except ValueError as exception:
        raise RuntimeError("TABVIO_MAX_CONCURRENT_RUNS must be an integer") from exception

    if value < 1:
        raise RuntimeError("TABVIO_MAX_CONCURRENT_RUNS must be at least 1")
    return value


def read_follow_up_window_seconds(default: int) -> int:
    configured_value = os.getenv("TABVIO_FOLLOW_UP_WINDOW_SECONDS", str(default))
    try:
        value = int(configured_value)
    except ValueError as exception:
        raise RuntimeError("TABVIO_FOLLOW_UP_WINDOW_SECONDS must be an integer") from exception

    if value < 1:
        raise RuntimeError("TABVIO_FOLLOW_UP_WINDOW_SECONDS must be at least 1")
    return value


def read_required_setting(name: str) -> str:
    configured_value = os.getenv(name, "").strip()
    if not configured_value:
        raise RuntimeError(f"{name} must be set before users can sign in")
    return configured_value


def read_workos_api_key() -> str:
    return read_required_setting("WORKOS_API_KEY")


def read_workos_client_id() -> str:
    return read_required_setting("WORKOS_CLIENT_ID")


def read_workos_redirect_uri_override() -> str | None:
    """An explicit redirect URI, for when the request origin is not the real one.

    Normally left unset: the callback URL is derived from the incoming request,
    so local and deployed environments each build their own without config.
    Set this only behind a proxy that rewrites the host into something the
    browser never sees.
    """
    configured_value = os.getenv("WORKOS_REDIRECT_URI", "").strip()
    return configured_value or None


def read_workos_cookie_password() -> str:
    """Read the key that seals the session cookie.

    WorkOS seals cookies with Fernet, so this must be a Fernet key rather than
    any thirty-two character string. Generate one with:

        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """
    from cryptography.fernet import Fernet

    configured_value = read_required_setting("WORKOS_COOKIE_PASSWORD")
    try:
        Fernet(configured_value)
    except Exception as exception:
        raise RuntimeError(
            "WORKOS_COOKIE_PASSWORD must be a Fernet key. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        ) from exception

    return configured_value
