import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tabvio.runs import constants
from tabvio.runs.exceptions import RunNotFoundError
from tabvio.runs.models import RunContext, RunRecord, RunStatus
from tabvio.runs.repository import RunRepository
from tabvio.runs.service import RunManager


class RecoveringBrowser:
    def __init__(self) -> None:
        self.capture_attempts = 0

    async def capture_frame(self) -> bytes:
        self.capture_attempts += 1
        if self.capture_attempts == 1:
            raise RuntimeError("temporary screenshot failure")

        return b"jpeg-frame"


class FrameCaptureTests(unittest.IsolatedAsyncioTestCase):
    WAIT_TIMEOUT_SECONDS = 1.0
    TEST_INTERVAL_SECONDS = 0.01

    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self._temporary_directory.name) / "tabvio.db"
        self._repository = RunRepository(database_path)
        self._repository.initialize()
        self._manager = RunManager(self._repository)
        self._constant_patchers = [
            patch.object(constants, "FRAME_CAPTURE_TIMEOUT_SECONDS", self.WAIT_TIMEOUT_SECONDS),
            patch.object(constants, "FRAME_RETRY_INTERVAL_SECONDS", self.TEST_INTERVAL_SECONDS),
            patch.object(constants, "FRAME_INTERVAL_SECONDS", self.TEST_INTERVAL_SECONDS),
        ]
        for constant_patcher in self._constant_patchers:
            constant_patcher.start()
            self.addCleanup(constant_patcher.stop)

    async def asyncTearDown(self) -> None:
        self._temporary_directory.cleanup()

    async def test_capture_retries_and_recovers_after_a_failure(self) -> None:
        run = RunRecord(task="Capture a frame", max_runtime_seconds=300)
        browser = RecoveringBrowser()
        context = RunContext(
            run=run,
            runtime=SimpleNamespace(browser=browser),
        )
        self._repository.save_run(run)
        self._manager._contexts[run.id] = context

        capture_task = asyncio.create_task(self._manager._capture_frames(context))
        await asyncio.wait_for(
            self._wait_for_latest_frame(context),
            timeout=self.WAIT_TIMEOUT_SECONDS,
        )
        run.status = RunStatus.SUCCEEDED
        await capture_task

        event_types = [event.event_type for event in context.events]
        self.assertEqual(context.latest_frame, b"jpeg-frame")
        self.assertGreaterEqual(browser.capture_attempts, 2)
        self.assertIn("browser.capture.failed", event_types)
        self.assertIn("browser.capture.recovered", event_types)
        self.assertEqual(self._manager.get_latest_frame(run.id), b"jpeg-frame")

    async def test_latest_frame_distinguishes_unknown_and_uncached_runs(self) -> None:
        run = RunRecord(task="Stored run", max_runtime_seconds=300)
        self._repository.save_run(run)

        self.assertIsNone(self._manager.get_latest_frame(run.id))
        with self.assertRaises(RunNotFoundError):
            self._manager.get_latest_frame(
                RunRecord(
                    task="Unknown run",
                    max_runtime_seconds=300,
                ).id
            )

    async def _wait_for_latest_frame(self, context: RunContext) -> None:
        while context.latest_frame is None:
            await asyncio.sleep(self.TEST_INTERVAL_SECONDS)


if __name__ == "__main__":
    unittest.main()
