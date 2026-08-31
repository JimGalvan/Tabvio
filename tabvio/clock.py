"""The one source of "now" in the application."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)
