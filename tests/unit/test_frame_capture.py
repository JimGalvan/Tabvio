import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from tabvio.runs.exceptions import RunNotFoundError
from tabvio.runs.models import RunRecord
from tabvio.runs.repository import RunRepository
from tabvio.runs.service import RunManager


class FrameAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self._temporary_directory.name) / "tabvio.db"
        self._repository = RunRepository(database_path)
        self._repository.initialize()
        self._manager = RunManager(self._repository)
        self._owner_id = uuid4()


    async def asyncTearDown(self) -> None:
        self._temporary_directory.cleanup()

    async def test_latest_frame_distinguishes_unknown_and_uncached_runs(self) -> None:
        run = RunRecord(
            task="Stored run", max_runtime_seconds=300, user_id=self._owner_id
        )
        self._repository.save_run(run)

        self.assertIsNone(self._manager.get_latest_frame(run.id, self._owner_id))
        with self.assertRaises(RunNotFoundError):
            self._manager.get_latest_frame(uuid4(), self._owner_id)

    async def test_another_accounts_frames_are_refused(self) -> None:
        run = RunRecord(
            task="Stored run", max_runtime_seconds=300, user_id=self._owner_id
        )
        self._repository.save_run(run)

        with self.assertRaises(RunNotFoundError):
            self._manager.get_latest_frame(run.id, uuid4())
