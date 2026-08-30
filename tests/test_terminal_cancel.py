import tempfile
import unittest
from pathlib import Path

from tabvio.runs.models import RunRecord, RunStatus
from tabvio.runs.repository import RunRepository
from tabvio.runs.service import RunManager


class TerminalCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self._temporary_directory.name) / "tabvio.db"
        self._repository = RunRepository(database_path)
        self._repository.initialize()
        self._manager = RunManager(self._repository)

    async def asyncTearDown(self) -> None:
        self._temporary_directory.cleanup()

    async def test_cancelling_a_cleaned_terminal_run_is_idempotent(self) -> None:
        run = RunRecord(
            task="Completed task",
            max_runtime_seconds=300,
            status=RunStatus.SUCCEEDED,
            final_output="Done",
        )
        self._repository.save_run(run)

        returned_run = await self._manager.cancel_run(run.id)

        self.assertEqual(returned_run.id, run.id)
        self.assertEqual(returned_run.status, RunStatus.SUCCEEDED)


if __name__ == "__main__":
    unittest.main()
