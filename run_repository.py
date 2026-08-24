import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID

from run_models import RunEvent, RunRecord, RunStatus


class RunRepository:
    def __init__(self, database_path: Path):
        self._database_path = database_path

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)

        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    max_runtime_seconds INTEGER NOT NULL,
                    final_output TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, sequence),
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_run_events_run_sequence
                ON run_events(run_id, sequence);
                """
            )

    def save_run(self, run: RunRecord) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    id,
                    thread_id,
                    task,
                    status,
                    max_runtime_seconds,
                    final_output,
                    error,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    task = excluded.task,
                    status = excluded.status,
                    max_runtime_seconds = excluded.max_runtime_seconds,
                    final_output = excluded.final_output,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    str(run.id),
                    str(run.thread_id),
                    run.task,
                    run.status.value,
                    run.max_runtime_seconds,
                    run.final_output,
                    run.error,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )
            connection.commit()

    def get_run(self, run_id: UUID) -> RunRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()

        if row is None:
            return None

        return RunRecord(
            id=row["id"],
            thread_id=row["thread_id"],
            task=row["task"],
            status=RunStatus(row["status"]),
            max_runtime_seconds=row["max_runtime_seconds"],
            final_output=row["final_output"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def save_event(self, event: RunEvent) -> None:
        payload_json = json.dumps(event.payload, ensure_ascii=False)

        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO run_events (
                    id,
                    run_id,
                    sequence,
                    event_type,
                    payload_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.id),
                    str(event.run_id),
                    event.sequence,
                    event.event_type,
                    payload_json,
                    event.created_at.isoformat(),
                ),
            )
            connection.commit()

    def list_events(
        self,
        run_id: UUID,
        after_sequence: int = 0,
    ) -> list[RunEvent]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM run_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (str(run_id), after_sequence),
            ).fetchall()

        events = []
        for row in rows:
            events.append(
                RunEvent(
                    id=row["id"],
                    run_id=row["run_id"],
                    sequence=row["sequence"],
                    event_type=row["event_type"],
                    payload=json.loads(row["payload_json"]),
                    created_at=row["created_at"],
                )
            )

        return events

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
