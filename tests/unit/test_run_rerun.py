import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tabvio.credentials.exceptions import CredentialNotFoundError
from tabvio.runs.exceptions import RunNotFoundError, RunNotRerunnableError
from tabvio.runs.models import RunRecord, RunStatus
from tabvio.runs.repository import RunRepository
from tabvio.runs.service import RunManager


class StubRuntime:
    """Enough of an AgentRuntime that create_run can start without a browser."""

    def __init__(self):
        self.config = {"configurable": {"thread_id": "stub"}}
        self.browser = self
        self.context = None
        self.sensitive_inputs = None

    async def close(self) -> None:
        return None


class StubCredentialService:
    def __init__(self, missing_ids: set | None = None):
        self._missing_ids = missing_ids or set()

    def require_selected(self, credential_ids, user_id):
        for credential_id in credential_ids:
            if credential_id in self._missing_ids:
                raise CredentialNotFoundError("Credential was not found")
        return []


class RunRerunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self._repository = RunRepository(
            Path(self._temporary_directory.name) / "tabvio.db"
        )
        self._repository.initialize()
        self._owner_id = uuid4()
        self._credential_service = StubCredentialService()
        self._manager = RunManager(
            self._repository,
            credential_service=self._credential_service,
            max_concurrent_runs=10,
        )

    async def asyncTearDown(self) -> None:
        for context in list(self._manager._contexts.values()):
            if context.execution_task is not None:
                context.execution_task.cancel()
            if context.capture_task is not None:
                context.capture_task.cancel()
        self._temporary_directory.cleanup()

    def _store_run(self, status: RunStatus, credential_ids=None) -> RunRecord:
        run = RunRecord(
            task="Check the order status",
            max_runtime_seconds=420,
            user_id=self._owner_id,
            status=status,
            credential_ids=credential_ids or [],
            final_output="Done",
        )
        self._repository.save_run(run)
        return run

    async def _rerun(self, run):
        with patch("tabvio.runs.service.build_agent_runtime", return_value=StubRuntime()):
            with patch.object(RunManager, "_execute", return_value=None):
                with patch.object(RunManager, "_capture_frames", return_value=None):
                    return await self._manager.rerun_run(run.id, self._owner_id)

    async def test_a_finished_run_starts_again_with_the_same_task(self) -> None:
        credential_ids = [uuid4()]
        original = self._store_run(RunStatus.SUCCEEDED, credential_ids)

        repeat = await self._rerun(original)

        self.assertNotEqual(repeat.id, original.id)
        self.assertEqual(repeat.task, original.task)
        self.assertEqual(repeat.max_runtime_seconds, original.max_runtime_seconds)
        self.assertEqual(repeat.credential_ids, credential_ids)
        self.assertEqual(repeat.user_id, self._owner_id)

    async def test_the_original_run_is_left_untouched(self) -> None:
        original = self._store_run(RunStatus.CANCELLED)

        await self._rerun(original)

        stored_original = self._repository.get_run(original.id)
        self.assertEqual(stored_original.status, RunStatus.CANCELLED)
        self.assertEqual(stored_original.final_output, "Done")

    async def test_every_terminal_status_can_be_started_again(self) -> None:
        for status in (
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        ):
            with self.subTest(status=status):
                original = self._store_run(status)
                repeat = await self._rerun(original)
                self.assertEqual(repeat.status, RunStatus.QUEUED)

    async def test_a_run_still_in_flight_cannot_be_started_again(self) -> None:
        for status in (
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            RunStatus.WAITING_FOR_INPUT,
            RunStatus.READY_FOR_FOLLOW_UP,
        ):
            with self.subTest(status=status):
                original = self._store_run(status)
                with self.assertRaises(RunNotRerunnableError):
                    await self._rerun(original)

    async def test_a_revoked_credential_is_reported_clearly(self) -> None:
        credential_id = uuid4()
        original = self._store_run(RunStatus.SUCCEEDED, [credential_id])
        self._manager._credential_service = StubCredentialService({credential_id})

        with self.assertRaises(RunNotRerunnableError) as caught:
            await self._rerun(original)

        self.assertIn("no longer available", str(caught.exception))

    async def test_another_accounts_run_cannot_be_started_again(self) -> None:
        original = self._store_run(RunStatus.SUCCEEDED)

        with self.assertRaises(RunNotFoundError):
            await self._manager.rerun_run(original.id, uuid4())


if __name__ == "__main__":
    unittest.main()
