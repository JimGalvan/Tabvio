import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from tabvio.runs.exceptions import RunCapacityReachedError, RunNotReadyForFollowUpError
from tabvio.runs.models import RunContext, RunRecord, RunStatus
from tabvio.runs.repository import RunRepository
from tabvio.runs.service import RunManager


class FollowUpBrowser:
    def __init__(self) -> None:
        self.closed = False
        self.capture_count = 0

    async def capture_frame(self) -> bytes:
        self.capture_count += 1
        return b"browser-frame"

    async def close(self) -> None:
        self.closed = True


class FollowUpAgent:
    def __init__(self) -> None:
        self.inputs = []

    async def astream(self, agent_input, **stream_options):
        self.inputs.append(agent_input)
        if False:
            yield stream_options

    async def aget_state(self, config):
        output = f"Result {len(self.inputs)}"
        return SimpleNamespace(values={"messages": [SimpleNamespace(content=output)]})


class FollowUpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self._temporary_directory.name) / "tabvio.db"
        self._repository = RunRepository(database_path)
        self._repository.initialize()
        self._owner_id = uuid4()

    async def asyncTearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _build_context(
        self,
        manager: RunManager,
    ) -> tuple[RunContext, FollowUpAgent, FollowUpBrowser]:
        run = RunRecord(task="First task", max_runtime_seconds=60, user_id=self._owner_id)
        agent = FollowUpAgent()
        browser = FollowUpBrowser()
        runtime = SimpleNamespace(
            agent=agent,
            browser=browser,
            config={"configurable": {"thread_id": str(run.thread_id)}},
        )
        context = RunContext(run=run, runtime=runtime)
        self._repository.save_run(run)
        manager._contexts[run.id] = context
        manager._active_run_ids.add(run.id)
        return context, agent, browser

    async def test_follow_up_reuses_runtime_and_end_releases_capacity(self) -> None:
        manager = RunManager(
            self._repository,
            max_concurrent_runs=1,
            follow_up_window_seconds=30,
        )
        context, agent, browser = self._build_context(manager)

        await manager._execute(
            context,
            {"messages": [{"role": "user", "content": "First task"}]},
        )

        self.assertEqual(context.run.status, RunStatus.READY_FOR_FOLLOW_UP)
        self.assertIn(context.run.id, manager._contexts)
        self.assertIn(context.run.id, manager._active_run_ids)
        self.assertFalse(browser.closed)
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

        self.assertEqual(len(agent.inputs), 2)
        follow_up_message = agent.inputs[1]["messages"][0]
        self.assertEqual(
            follow_up_message["content"],
            "Add the first item to the cart",
        )
        self.assertFalse(browser.closed)

        ended_run = await manager.end_session(context.run.id, self._owner_id)

        self.assertEqual(ended_run.status, RunStatus.SUCCEEDED)
        self.assertIsNone(ended_run.follow_up_expires_at)
        self.assertTrue(browser.closed)
        self.assertNotIn(context.run.id, manager._contexts)
        self.assertNotIn(context.run.id, manager._active_run_ids)

    async def test_follow_up_window_expiry_closes_browser(self) -> None:
        manager = RunManager(
            self._repository,
            follow_up_window_seconds=0.01,
        )
        context, _, browser = self._build_context(manager)

        await manager._execute(
            context,
            {"messages": [{"role": "user", "content": "First task"}]},
        )
        await self._wait_for_context_cleanup(manager, context)

        self.assertEqual(context.run.status, RunStatus.SUCCEEDED)
        self.assertIsNone(context.run.follow_up_expires_at)
        self.assertTrue(browser.closed)
        self.assertNotIn(context.run.id, manager._active_run_ids)

        stored_events = self._repository.list_events(context.run.id)
        self.assertIn(
            "follow_up.expired",
            [event.event_type for event in stored_events],
        )

    async def test_follow_up_requires_ready_status(self) -> None:
        manager = RunManager(self._repository)
        context, _, _ = self._build_context(manager)

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
