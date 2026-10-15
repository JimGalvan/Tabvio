import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from tabvio_agent.events import (
    AgentMessageDeltaEvent,
    BrowserControlAppliedEvent,
    InputRequiredEvent,
    RunCompletedEvent,
    RunFailedEvent,
    SensitiveInputCancelledEvent,
    SensitiveInputRequiredEvent,
    SessionStatusEvent,
    TaskTodosEvent,
)
from tabvio_agent.models import Todo

from tabvio.runs import constants
from tabvio.runs.models import BrowserControlEvent, RunStatus
from tabvio.runs.repository import RunRepository
from tabvio.runs.service import RunManager
from tests.session_support import StubSession


class SessionEventBookkeepingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = RunRepository(Path(self.directory.name) / "tabvio.db")
        self.repository.initialize()
        self.manager = RunManager(self.repository)
        self.addAsyncCleanup(self.manager.shutdown)
        self.owner = uuid4()
        self.session = StubSession(complete_turns=False)
        with patch("tabvio.runs.service.build_agent_runtime", return_value=self.session):
            self.run = await self.manager.create_run("First task", 60, self.owner)
        self.context = self.manager._contexts[self.run.id]

    async def emit(self, *events):
        for event in events:
            await self.session._events.put(event)

    async def status(self, expected):
        async with asyncio.timeout(2):
            async with self.context.event_condition:
                while self.run.status != expected:
                    await self.context.event_condition.wait()

    async def test_question_events_keep_the_existing_payload_and_status_order(self):
        await self.emit(InputRequiredEvent("Which size?"), SessionStatusEvent("waiting_for_input"))
        await self.status(RunStatus.WAITING_FOR_INPUT)
        events = self.repository.list_events(self.run.id)
        self.assertEqual(events[-2].event_type, "input.required")
        self.assertEqual(events[-2].payload, {"question": "Which size?"})
        self.assertEqual(events[-1].payload, {"status": "waiting_for_input"})
        self.session.answer = AsyncMock()
        await self.manager.submit_input(self.run.id, self.owner, "M")
        self.session.answer.assert_awaited_once_with("M")

    async def test_payment_signals_and_todos_keep_the_existing_payload_shape(self):
        await self.emit(
            InputRequiredEvent("Pay?", ("amount:20",)),
            TaskTodosEvent((Todo("Read page", "completed"),)), SessionStatusEvent("waiting_for_input"),
        )
        await self.status(RunStatus.WAITING_FOR_INPUT)
        events = self.repository.list_events(self.run.id)
        self.assertEqual(events[-3].payload, {"question": "Pay?", "payment_signals": ["amount:20"]})
        self.assertEqual(events[-2].payload, {"todos": [{"content": "Read page", "status": "completed"}]})

    async def test_sensitive_requests_are_forwarded_and_declines_use_the_event_request_id(self):
        request_id = uuid4()
        deadline = datetime.now(UTC)
        await self.emit(
            SensitiveInputRequiredEvent(request_id, "mfa_code", "Enter code", deadline),
            SessionStatusEvent("waiting_for_input"),
        )
        await self.status(RunStatus.WAITING_FOR_INPUT)
        event = self.repository.list_events(self.run.id)[-2]
        self.assertEqual(event.payload, {
            "request_id": str(request_id), "kind": "mfa_code", "prompt": "Enter code", "expires_at": deadline.isoformat(),
        })
        self.session.decline_sensitive_input = AsyncMock()
        await self.manager.decline_sensitive_input(self.run.id, self.owner)
        self.session.decline_sensitive_input.assert_awaited_once_with(request_id)
        await self.emit(SensitiveInputCancelledEvent(request_id, "declined"), SessionStatusEvent("running"))
        await self.status(RunStatus.RUNNING)
        self.assertIsNone(self.context.sensitive_input_request_id)

    async def test_message_deltas_are_live_only_and_trimmed(self):
        with patch.object(constants, "MAX_LIVE_MESSAGE_EVENT_COUNT", 2):
            await self.emit(
                AgentMessageDeltaEvent("one"), AgentMessageDeltaEvent("two"), AgentMessageDeltaEvent("three"),
                RunCompletedEvent("Final"), SessionStatusEvent("ready_for_follow_up"),
            )
            await self.status(RunStatus.READY_FOR_FOLLOW_UP)
        deltas = []
        for event in self.context.events:
            if event.event_type == "agent.message.delta":
                deltas.append(event.payload["text"])
        self.assertEqual(deltas, ["two", "three"])
        for event in self.repository.list_events(self.run.id):
            self.assertNotEqual(event.event_type, "agent.message.delta")

    async def test_completed_payload_includes_the_app_follow_up_deadline(self):
        await self.emit(RunCompletedEvent("Done"), SessionStatusEvent("ready_for_follow_up"))
        await self.status(RunStatus.READY_FOR_FOLLOW_UP)
        completed = self.repository.list_events(self.run.id)[-2]
        self.assertEqual(completed.payload, {"output": "Done", "follow_up_expires_at": self.run.follow_up_expires_at.isoformat()})
        self.assertEqual(self.repository.get_run(self.run.id).final_output, "Done")
        self.assertIn(self.run.id, self.manager._active_run_ids)

    async def test_failed_events_persist_errors_and_release_capacity(self):
        await self.emit(RunFailedEvent("model died"), SessionStatusEvent("failed"))
        await self.context.event_task
        self.assertEqual(self.run.status, RunStatus.FAILED)
        self.assertEqual(self.repository.get_run(self.run.id).error, "model died")
        self.assertNotIn(self.run.id, self.manager._active_run_ids)
        self.assertNotIn(self.run.id, self.manager._contexts)

    async def test_event_persistence_failure_fails_the_run_and_releases_capacity(self):
        save_event = self.repository.save_event

        def fail_once(event):
            if event.event_type == "input.required":
                raise RuntimeError("Persistence unavailable")
            save_event(event)

        with patch.object(self.repository, "save_event", side_effect=fail_once):
            await self.emit(InputRequiredEvent("Continue?"), SessionStatusEvent("waiting_for_input"))
            await self.context.event_task

        stored_run = self.repository.get_run(self.run.id)
        self.assertEqual(stored_run.status, RunStatus.FAILED)
        self.assertEqual(stored_run.error, "Persistence unavailable")
        self.assertIsNone(stored_run.follow_up_expires_at)
        events = self.repository.list_events(self.run.id)
        self.assertEqual(events[-2].event_type, "run.failed")
        self.assertEqual(events[-2].payload, {"error": "Persistence unavailable"})
        self.assertEqual(events[-1].payload, {"status": "failed"})
        self.session.close.assert_awaited_once()
        self.assertTrue(self.context.frame_task.done())
        self.assertNotIn(self.run.id, self.manager._active_run_ids)
        self.assertNotIn(self.run.id, self.manager._contexts)

    async def test_timeout_status_preserves_the_existing_failed_event_payload(self):
        await self.emit(RunFailedEvent("The run exceeded its execution time limit"), SessionStatusEvent("timed_out"))
        await self.context.event_task
        self.assertEqual(self.run.status, RunStatus.TIMED_OUT)
        self.assertIsNone(self.run.error)
        events = self.repository.list_events(self.run.id)
        self.assertEqual(events[-2].event_type, "run.failed")
        self.assertEqual(events[-2].payload, {"error": "The run exceeded its execution time limit"})

    async def test_cancel_waits_for_persisted_terminal_state_and_cleanup(self):
        returned = await self.manager.cancel_run(self.run.id, self.owner)
        self.assertEqual(returned.status, RunStatus.CANCELLED)
        self.assertEqual(self.repository.get_run(self.run.id).status, RunStatus.CANCELLED)
        self.assertNotIn(self.run.id, self.manager._contexts)
        self.assertTrue(self.context.event_task.done())
        self.assertTrue(self.context.frame_task.done())

    async def test_control_results_acknowledge_the_socket_without_becoming_sse_events(self):
        await self.emit(InputRequiredEvent("Continue?"), SessionStatusEvent("waiting_for_input"))
        await self.status(RunStatus.WAITING_FOR_INPUT)
        async def applied(action):
            await self.emit(BrowserControlAppliedEvent("Clicked"))
            return "Clicked"
        self.session.apply_control = AsyncMock(side_effect=applied)
        detail = await self.manager.apply_control(self.context, BrowserControlEvent(type="click", x=10, y=20))
        self.assertEqual(detail, "Clicked")
        for event in self.context.events:
            self.assertNotEqual(event.event_type, "browser.control.applied")

    async def test_ending_during_control_returns_the_result_and_releases_the_lock(self):
        await self.emit(RunCompletedEvent("Done"), SessionStatusEvent("ready_for_follow_up"))
        await self.status(RunStatus.READY_FOR_FOLLOW_UP)
        entered = asyncio.Event()
        proceed = asyncio.Event()

        async def applied(action):
            entered.set()
            await proceed.wait()
            await self.emit(BrowserControlAppliedEvent("Clicked"))
            return "Clicked"

        self.session.apply_control = AsyncMock(side_effect=applied)
        applying = asyncio.create_task(self.manager.apply_control(self.context, BrowserControlEvent(type="click")))
        await entered.wait()
        await self.manager.end_session(self.run.id, self.owner)
        self.assertTrue(self.context.event_task.done())
        proceed.set()
        async with asyncio.timeout(2):
            self.assertEqual(await applying, "Clicked")
        self.assertFalse(self.context.control_lock.locked())

    async def test_frame_fan_out_and_terminal_cache_keep_the_latest_frame(self):
        first_viewer = self.manager.stream_frames(self.run.id, self.owner)
        second_viewer = self.manager.stream_frames(self.run.id, self.owner)
        await self.session.emit_frame(b"frame")
        self.assertEqual(await anext(first_viewer), b"frame")
        self.assertEqual(await anext(second_viewer), b"frame")
        await self.manager.cancel_run(self.run.id, self.owner)
        self.assertEqual(self.manager.get_latest_frame(self.run.id, self.owner), b"frame")
        for viewer in (first_viewer, second_viewer):
            with self.assertRaises(StopAsyncIteration):
                await anext(viewer)

    async def test_event_stream_replays_persisted_terminal_events(self):
        await self.manager.cancel_run(self.run.id, self.owner)
        events = []
        async for event in self.manager.stream_events(self.run.id, self.owner, after_sequence=1):
            events.append(event.event_type)
        self.assertEqual(events, ["run.cancelled", "run.status"])
