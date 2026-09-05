import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from tabvio.runs import constants
from tabvio.runs.exceptions import RunCapacityReachedError
from tabvio.runs.models import RunContext, RunRecord, RunStatus
from tabvio.runs.repository import RunRepository
from tabvio.runs.service import RunManager


class ClosingBrowser:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class ConcurrentRunTests(unittest.IsolatedAsyncioTestCase):
    SEQUENTIAL_RUN_COUNT = 50

    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self._temporary_directory.name) / "tabvio.db"
        self._repository = RunRepository(database_path)
        self._repository.initialize()
        self._manager = RunManager(
            self._repository,
            max_concurrent_runs=3,
        )

    async def asyncTearDown(self) -> None:
        self._temporary_directory.cleanup()

    async def test_fourth_run_is_rejected_at_capacity(self) -> None:
        self._manager._active_run_ids = {
            uuid4(),
            uuid4(),
            uuid4(),
        }

        with self.assertRaises(RunCapacityReachedError):
            await self._manager.create_run(
                task="One run too many",
                max_runtime_seconds=300,
            )

    async def test_terminal_runs_release_contexts_and_bound_frame_memory(self) -> None:
        closed_browsers = []

        for run_number in range(self.SEQUENTIAL_RUN_COUNT):
            run = RunRecord(
                task=f"Run {run_number}",
                max_runtime_seconds=300,
                status=RunStatus.SUCCEEDED,
            )
            browser = ClosingBrowser()
            context = RunContext(
                run=run,
                runtime=SimpleNamespace(browser=browser),
                latest_frame=f"frame-{run_number}".encode(),
            )
            self._repository.save_run(run)
            self._manager._contexts[run.id] = context
            self._manager._active_run_ids.add(run.id)
            self._manager._resuming_run_ids.add(run.id)

            await self._manager._finish_context(context)
            closed_browsers.append(browser)

        self.assertEqual(self._manager._contexts, {})
        self.assertEqual(self._manager._active_run_ids, set())
        self.assertEqual(self._manager._resuming_run_ids, set())
        self.assertLessEqual(
            len(self._manager._completed_frames),
            constants.MAX_COMPLETED_FRAME_COUNT,
        )
        self.assertTrue(all(browser.closed for browser in closed_browsers))

    async def test_concurrency_limit_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            RunManager(self._repository, max_concurrent_runs=0)


if __name__ == "__main__":
    unittest.main()
