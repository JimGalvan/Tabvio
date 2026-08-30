"""Application settings and filesystem paths."""

import os
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
STATIC_DIRECTORY = Path(__file__).resolve().parent / "server" / "static"
DATABASE_PATH = PROJECT_DIRECTORY / "data" / "tabvio.db"


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
