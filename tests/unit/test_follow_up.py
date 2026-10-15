import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tabvio.runs.exceptions import RunCapacityReachedError, RunNotReadyForFollowUpError
from tabvio.runs.models import RunContext, RunStatus
from tabvio.runs.repository import RunRepository
from tabvio.runs.service import RunManager
from tests.session_support import StubSession


class FollowUpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self._temporary_directory.name) / "tabvio.db"
        self._repository = RunRepository(database_path)
        self._repository.initialize()
        self._owner_id = uuid4()

    async def asyncTearDown(self) -> None:
        self._temporary_directory.cleanup()

    async def _build_context(self, manager):
        session = StubSession()
        with patch("tabvio.runs.service.build_agent_runtime", return_value=session):
            run = await manager.create_run("First task", 60, self._owner_id)
        context = manager._contexts[run.id]
        await self._wait_for_status(context, RunStatus.READY_FOR_FOLLOW_UP)
        return context, session

    async def test_follow_up_reuses_runtime_and_end_releases_capacity(self) -> None:
        manager = RunManager(
            self._repository,
            max_concurrent_runs=1,
            follow_up_window_seconds=30,
        )
        context, session = await self._build_context(manager)

        self.assertEqual(context.run.status, RunStatus.READY_FOR_FOLLOW_UP)
        self.assertIn(context.run.id, manager._contexts)
        self.assertIn(context.run.id, manager._active_run_ids)
        session.close.assert_not_awaited()
        self.assertIsNotNone(context.run.follow_up_expires_at)

        with self.assertRaises(RunCapacityReachedError):
            await manager.create_run("Another session", 60)

        await manager.submit_follow_up(
            context.run.id,
            self._owner_id,
            "Add the first item to the cart",
        )
        await self._wait_for_status(
            context,
            RunStatus.READY_FOR_FOLLOW_UP,
        )

        self.assertEqual(len(session.inputs), 2)
        self.assertEqual(
            session.inputs[1],
            "Add the first item to the cart",
        )
        session.close.assert_not_awaited()

        ended_run = await manager.end_session(context.run.id, self._owner_id)

        self.assertEqual(ended_run.status, RunStatus.SUCCEEDED)
        self.assertIsNone(ended_run.follow_up_expires_at)
        session.close.assert_awaited_once()
        self.assertNotIn(context.run.id, manager._contexts)
        self.assertNotIn(context.run.id, manager._active_run_ids)

    async def test_follow_up_window_expiry_closes_browser(self) -> None:
        manager = RunManager(
            self._repository,
            follow_up_window_seconds=0.01,
        )
        context, session = await self._build_context(manager)
        await self._wait_for_context_cleanup(manager, context)

        self.assertEqual(context.run.status, RunStatus.SUCCEEDED)
        self.assertIsNone(context.run.follow_up_expires_at)
        session.close.assert_awaited_once()
        self.assertNotIn(context.run.id, manager._active_run_ids)

        stored_events = self._repository.list_events(context.run.id)
        self.assertIn(
            "follow_up.expired",
            [event.event_type for event in stored_events],
        )

    async def test_follow_up_requires_ready_status(self) -> None:
        manager = RunManager(self._repository)
        context, session = await self._build_context(manager)
        context.run.status = RunStatus.RUNNING

        with self.assertRaises(RunNotReadyForFollowUpError):
            await manager.submit_follow_up(context.run.id, self._owner_id, "Another task")

        await manager.shutdown()

    async def test_follow_up_window_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            RunManager(
                self._repository,
                follow_up_window_seconds=0,
            )

    async def _wait_for_status(
        self,
        context: RunContext,
        expected_status: RunStatus,
    ) -> None:
        check_interval_seconds = 0.01
        max_check_count = 100
        for _ in range(max_check_count):
            if (
                context.run.status == expected_status
                and context.follow_up_expiry_task is not None
            ):
                return
            await asyncio.sleep(check_interval_seconds)

        self.fail(f"Run did not reach {expected_status}")

    async def _wait_for_context_cleanup(
        self,
        manager: RunManager,
        context: RunContext,
    ) -> None:
        check_interval_seconds = 0.01
        max_check_count = 100
        for _ in range(max_check_count):
            if context.run.id not in manager._contexts:
                return
            await asyncio.sleep(check_interval_seconds)

        self.fail("Run context was not cleaned up")


if __name__ == "__main__":
    unittest.main()
