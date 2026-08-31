import sqlite3
from contextlib import closing
from pathlib import Path

from tabvio.auth.models import User


class UserRepository:
    def __init__(self, database_path: Path):
        self._database_path = database_path

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)

        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    workos_user_id TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_users_workos_user_id
                ON users(workos_user_id);
                """
            )
            connection.commit()

    def get_or_create_user(self, workos_user_id: str, email: str) -> User:
        """Return the local account for a WorkOS user, creating it on first sign-in."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE workos_user_id = ?",
                (workos_user_id,),
            ).fetchone()

            if row is not None:
                if row["email"] != email:
                    connection.execute(
                        "UPDATE users SET email = ? WHERE id = ?",
                        (email, row["id"]),
                    )
                    connection.commit()

                return User(
                    id=row["id"],
                    workos_user_id=row["workos_user_id"],
                    email=email,
                    created_at=row["created_at"],
                )

            user = User(workos_user_id=workos_user_id, email=email)
            connection.execute(
                """
                INSERT INTO users (id, workos_user_id, email, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(user.id),
                    user.workos_user_id,
                    user.email,
                    user.created_at.isoformat(),
                ),
            )
            connection.commit()
            return user

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
