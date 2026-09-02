"""Application settings and filesystem paths."""

import base64
import binascii
import os
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
STATIC_DIRECTORY = Path(__file__).resolve().parent / "server" / "static"
DATABASE_PATH = PROJECT_DIRECTORY / "data" / "tabvio.db"
CREDENTIAL_KEY_LENGTH_BYTES = 32


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


def read_credential_encryption_key() -> bytes | None:
    """Read the key that encrypts saved browser credentials, when configured.

    This is a base64-encoded 32-byte key, kept separate from
    WORKOS_COOKIE_PASSWORD so that sessions and stored credentials never share
    one secret. Generate one with:

        openssl rand -base64 32

    Losing it makes every saved credential unrecoverable, so treat it as
    permanent rather than something to regenerate.
    """
    configured_value = os.getenv("TABVIO_CREDENTIAL_KEY", "").strip()
    if not configured_value:
        return None

    try:
        key = base64.b64decode(configured_value, validate=True)
    except (binascii.Error, ValueError) as exception:
        raise RuntimeError(
            "TABVIO_CREDENTIAL_KEY must be base64. Generate one with: openssl rand -base64 32"
        ) from exception

    if len(key) != CREDENTIAL_KEY_LENGTH_BYTES:
        raise RuntimeError(
            f"TABVIO_CREDENTIAL_KEY must decode to {CREDENTIAL_KEY_LENGTH_BYTES} bytes, "
            f"got {len(key)}. Generate one with: openssl rand -base64 32"
        )
    return key


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
