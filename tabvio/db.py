"""Shared SQLite connection settings.

Runs and accounts live in the same file and are read from both the request
path and the run threadpool, so every connection is opened in WAL mode with a
busy timeout. Without those, a run write and a session read landing together
fail outright with "database is locked".
"""

import sqlite3
from pathlib import Path

BUSY_TIMEOUT_SECONDS = 5.0


def connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=BUSY_TIMEOUT_SECONDS)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
