import tempfile
import unittest
from datetime import UTC
from pathlib import Path

from tabvio.runs.models import RunEvent, RunRecord, RunStatus, utc_now
from tabvio.runs.repository import RunRepository


class RunRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self._temporary_directory.name) / "tabvio.db"
        self._repository = RunRepository(database_path)
        self._repository.initialize()

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_run_and_ordered_events_are_persisted(self) -> None:
        run = RunRecord(task="Visit example.com", max_runtime_seconds=300)
        self._repository.save_run(run)

        first_event = RunEvent(
            run_id=run.id,
            sequence=1,
            event_type="run.created",
        )
        second_event = RunEvent(
            run_id=run.id,
            sequence=2,
            event_type="browser.navigation.completed",
            payload={"url": "https://example.com"},
        )
        self._repository.save_event(second_event)
        self._repository.save_event(first_event)

        stored_run = self._repository.get_run(run.id)
        stored_events = self._repository.list_events(run.id)

        self.assertIsNotNone(stored_run)
        self.assertEqual(stored_run.id, run.id)
        self.assertEqual(stored_run.thread_id, run.thread_id)
        self.assertEqual(
            [event.sequence for event in stored_events],
            [1, 2],
        )
        self.assertEqual(
            stored_events[1].payload,
            {"url": "https://example.com"},
        )

    def test_run_update_keeps_creation_time_and_records_update_time(self) -> None:
        run = RunRecord(task="Visit example.com", max_runtime_seconds=300)
        original_created_at = run.created_at
        self._repository.save_run(run)

        run.status = RunStatus.SUCCEEDED
        run.final_output = "Done"
        run.follow_up_expires_at = utc_now()
        run.updated_at = utc_now()
        self._repository.save_run(run)

        stored_run = self._repository.get_run(run.id)

        self.assertIsNotNone(stored_run)
        self.assertEqual(stored_run.created_at, original_created_at)
        self.assertEqual(stored_run.status, RunStatus.SUCCEEDED)
        self.assertEqual(stored_run.final_output, "Done")
        self.assertEqual(
            stored_run.follow_up_expires_at,
            run.follow_up_expires_at,
        )
        self.assertEqual(stored_run.created_at.utcoffset(), UTC.utcoffset(None))
        self.assertEqual(stored_run.updated_at.utcoffset(), UTC.utcoffset(None))


if __name__ == "__main__":
    unittest.main()
