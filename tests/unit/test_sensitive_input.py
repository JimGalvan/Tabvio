import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from tabvio.runs import constants
from tabvio.runs.exceptions import SensitiveInputNotPendingError
from tabvio.runs.sensitive_input import SensitiveInputChannel
from tabvio.runs.models import RunContext, RunRecord, RunStatus
from tabvio.runs.repository import RunRepository
from tabvio.runs.service import RunManager


class SensitiveBrowser:
    def __init__(self):
        self.fills = []

    async def fill_sensitive(self, element_index, value):
        self.fills.append((element_index, value))


class SensitiveInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self._repository = RunRepository(
            Path(self._temporary_directory.name) / "tabvio.db"
        )
        self._repository.initialize()
        self._manager = RunManager(self._repository)
        self._manager._execute = AsyncMock()

    async def asyncTearDown(self) -> None:
        self._temporary_directory.cleanup()

    def park_run_on_a_code_request(self, owner_id, prompt="Enter the code"):
        """A run waiting on the secure code box, as the dashboard would show it."""
        run = RunRecord(
            task="Sign in",
            max_runtime_seconds=300,
            user_id=owner_id,
            status=RunStatus.WAITING_FOR_INPUT,
        )
        browser = SensitiveBrowser()
        channel = SensitiveInputChannel()
        pending = channel.begin(8, prompt)
        context = RunContext(
            run=run,
            runtime=SimpleNamespace(browser=browser, sensitive_inputs=channel),
        )
        self._repository.save_run(run)
        self._manager._contexts[run.id] = context
        self._manager._active_run_ids.add(run.id)
        return context, pending, browser

    def resume_payload(self):
        return self._manager._execute.await_args.args[1].resume

    async def test_code_is_filled_but_never_persisted(self) -> None:
        owner_id = uuid4()
        context, pending, browser = self.park_run_on_a_code_request(owner_id)

        result = await self._manager.submit_sensitive_input(
            context.run.id, owner_id, pending.id, "123456"
        )
        await asyncio.sleep(0)

        self.assertIs(result, context.run)
        self.assertEqual(browser.fills, [(8, "123456")])
        events = self._repository.list_events(context.run.id)
        self.assertEqual(events[-1].event_type, "sensitive_input.received")
        self.assertNotIn("123456", events[-1].model_dump_json())
        self._manager._execute.assert_awaited_once()

    async def test_declining_sends_the_agent_back_without_a_code(self) -> None:
        owner_id = uuid4()
        context, _, browser = self.park_run_on_a_code_request(owner_id)

        await self._manager.decline_sensitive_input(context.run.id, owner_id)
        await asyncio.sleep(0)

        self.assertIsNone(context.runtime.sensitive_inputs.pending)
        self.assertEqual(browser.fills, [])
        self.assertEqual(
            self.resume_payload(),
            {"entered": False, "reason": constants.SENSITIVE_INPUT_DECLINED_REASON},
        )
        events = self._repository.list_events(context.run.id)
        self.assertEqual(events[-1].event_type, "sensitive_input.cancelled")

    async def test_declining_a_dropped_request_is_refused(self) -> None:
        owner_id = uuid4()
        context, _, _ = self.park_run_on_a_code_request(owner_id)
        await self._manager.decline_sensitive_input(context.run.id, owner_id)

        with self.assertRaises(SensitiveInputNotPendingError):
            await self._manager.decline_sensitive_input(context.run.id, owner_id)

    async def test_an_unanswered_code_request_expires(self) -> None:
        owner_id = uuid4()
        self._manager._sensitive_input_window_seconds = 0.01
        context, _, _ = self.park_run_on_a_code_request(owner_id)

        deadline = self._manager._start_sensitive_input_timeout(context)
        await asyncio.sleep(0.05)

        self.assertGreater(deadline, context.run.created_at)
        self.assertIsNone(context.runtime.sensitive_inputs.pending)
        self.assertEqual(
            self.resume_payload(),
            {"entered": False, "reason": constants.SENSITIVE_INPUT_TIMEOUT_REASON},
        )

    async def test_entering_a_code_stops_the_clock(self) -> None:
        owner_id = uuid4()
        self._manager._sensitive_input_window_seconds = 0.01
        context, pending, browser = self.park_run_on_a_code_request(owner_id)
        self._manager._start_sensitive_input_timeout(context)

        await self._manager.submit_sensitive_input(
            context.run.id, owner_id, pending.id, "123456"
        )
        await asyncio.sleep(0.05)

        self.assertEqual(browser.fills, [(8, "123456")])
        self.assertEqual(self.resume_payload(), {"entered": True})

    async def test_the_dashboard_is_told_when_the_code_window_closes(self) -> None:
        owner_id = uuid4()
        context, _, _ = self.park_run_on_a_code_request(owner_id)

        await self._manager._handle_stream_part(
            context,
            {
                "type": "custom",
                "data": {
                    "event_type": "sensitive_input.required",
                    "payload": {"request_id": str(uuid4()), "prompt": "Enter the code"},
                },
            },
        )
        self._manager._cancel_sensitive_input_timeout(context)

        published = self._repository.list_events(context.run.id)[-1]
        self.assertEqual(published.event_type, "sensitive_input.required")
        self.assertIn("expires_at", published.payload)


class SensitiveInputChannelTests(unittest.TestCase):
    """The channel keeps one request, and survives a withdrawn one being replayed."""

    def test_a_withdrawn_request_can_still_be_cleared(self) -> None:
        channel = SensitiveInputChannel()
        request = channel.begin(4, "Enter the code")

        channel.withdraw("the person walked away")

        self.assertIsNone(channel.pending)
        self.assertTrue(channel.is_withdrawn)
        channel.clear(request.id)
        self.assertFalse(channel.is_withdrawn)

    def test_the_reason_is_handed_over_only_once(self) -> None:
        channel = SensitiveInputChannel()
        channel.begin(4, "Enter the code")
        channel.withdraw("the person walked away")

        self.assertEqual(channel.take_withdrawn_reason(), "the person walked away")
        self.assertIsNone(channel.take_withdrawn_reason())


if __name__ == "__main__":
    unittest.main()
