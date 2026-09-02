from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID

from tabvio.credentials.models import CredentialRecord
from tabvio.db import connect


class CredentialRepository:
    def __init__(self, database_path: Path):
        self._database_path = database_path

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS credentials (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    allowed_domains_json TEXT NOT NULL,
                    login_hint TEXT NOT NULL,
                    encrypted_payload BLOB,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revoked_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_credentials_user
                ON credentials(user_id, revoked_at, name);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_credentials_active_name
                ON credentials(user_id, name COLLATE NOCASE)
                WHERE revoked_at IS NULL;
                """
            )
            credential_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(credentials)")
            }
            if "is_default" not in credential_columns:
                connection.execute(
                    "ALTER TABLE credentials ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0"
                )
            connection.commit()

    def save(self, credential: CredentialRecord) -> None:
        with closing(self._connect()) as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO credentials (
                        id, user_id, name, allowed_domains_json, login_hint,
                        encrypted_payload, is_default, created_at, updated_at, revoked_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        allowed_domains_json = excluded.allowed_domains_json,
                        login_hint = excluded.login_hint,
                        encrypted_payload = excluded.encrypted_payload,
                        is_default = excluded.is_default,
                        updated_at = excluded.updated_at,
                        revoked_at = excluded.revoked_at
                    """,
                    (
                        str(credential.id),
                        str(credential.user_id),
                        credential.name,
                        json.dumps(credential.allowed_domains),
                        credential.login_hint,
                        credential.encrypted_payload,
                        int(credential.is_default),
                        credential.created_at.isoformat(),
                        credential.updated_at.isoformat(),
                        credential.revoked_at.isoformat() if credential.revoked_at else None,
                    ),
                )
            except sqlite3.IntegrityError as exception:
                raise ValueError("A credential with that name already exists") from exception
            connection.commit()

    def get_owned(self, credential_id: UUID, user_id: UUID) -> CredentialRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM credentials
                WHERE id = ? AND user_id = ? AND revoked_at IS NULL
                """,
                (str(credential_id), str(user_id)),
            ).fetchone()
        return self._build(row) if row is not None else None

    def list_for_user(self, user_id: UUID) -> list[CredentialRecord]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM credentials
                WHERE user_id = ? AND revoked_at IS NULL
                ORDER BY is_default DESC, name COLLATE NOCASE, created_at
                """,
                (str(user_id),),
            ).fetchall()
        return [self._build(row) for row in rows]

    def revoke(self, credential_id: UUID, user_id: UUID, revoked_at: str) -> bool:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE credentials
                SET encrypted_payload = NULL, revoked_at = ?, updated_at = ?
                WHERE id = ? AND user_id = ? AND revoked_at IS NULL
                """,
                (revoked_at, revoked_at, str(credential_id), str(user_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    @staticmethod
    def _build(row: sqlite3.Row) -> CredentialRecord:
        return CredentialRecord(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            allowed_domains=json.loads(row["allowed_domains_json"]),
            login_hint=row["login_hint"],
            encrypted_payload=row["encrypted_payload"],
            is_default=bool(row["is_default"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            revoked_at=row["revoked_at"],
        )

    def _connect(self):
        return connect(self._database_path)
