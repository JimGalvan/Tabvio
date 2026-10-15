import asyncio
import unittest
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from tabvio_agent.exceptions import SessionNotWaitingForInputError
from tabvio_agent.models import ClickAction

from .session_support import build_session, next_event, ready
from .test_session_sensitive_input import sensitive_tools


class SessionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_preserves_a_completed_turn_while_frame_capture_pauses(self):
        session, browser = build_session(AIMessage(content="Done"))
        self.addAsyncCleanup(session.close)
        pausing = asyncio.Event()
        proceed = asyncio.Event()
        closing_started = asyncio.Event()
        pause_frame_capture = session._pause_frame_capture

        async def pause():
            pausing.set()
            await proceed.wait()
            await pause_frame_capture()

        async def close():
            closing_started.set()
            await session.close()

        events = session.events()
        with patch.object(session, "_pause_frame_capture", side_effect=pause):
            await session.start("Go")
            await ready(session, events)
            await pausing.wait()
            self.assertFalse(session._execution_task.done())
            closing = asyncio.create_task(close())
            await closing_started.wait()
            proceed.set()
            await closing

        remaining = []
        async for event in events:
            remaining.append(event.event_type)
        self.assertEqual(remaining, [])
        self.assertEqual(session._status, "ready_for_follow_up")
        self.assertTrue(session._execution_task.done())
        browser.close.assert_awaited_once()

    async def test_cancel_running_execution_emits_one_terminal_event(self):
        session, browser = build_session()
        self.addAsyncCleanup(session.close)
        entered = asyncio.Event()
        blocked = asyncio.Event()
        async def stream(value):
            entered.set()
            await blocked.wait()
            yield
        session._runtime.stream = stream
        await session.start("Go")
        await entered.wait()
        await session.cancel()
        await session.cancel()
        types = []
        async for event in session.events():
            types.append(event.event_type)
        self.assertEqual(types, ["run.status", "run.cancelled", "run.status"])
        browser.close.assert_awaited_once()
        self.assertTrue(session._execution_task.done())
        self.assertIsNone(session._capture_task)

    async def test_cancel_stops_an_answer_waiting_for_browser_control(self):
        call = {"name": "ask_person", "args": {"question": "Which size?"}, "id": str(uuid4())}
        session, browser = build_session(AIMessage(content="", tool_calls=[call]), AIMessage(content="Done"))
        self.addAsyncCleanup(session.close)
        events = session.events()
        await session.start("Buy it")
        await next_event(events, "input.required")
        await session._execution_task
        previous_execution = session._execution_task
        controlling = asyncio.Event()
        resuming = asyncio.Event()
        browser_closed = asyncio.Event()
        set_status = session._set_status

        async def click(*args):
            controlling.set()
            await browser_closed.wait()
            return "Clicked"

        async def status(value):
            if value == "running":
                resuming.set()
            await set_status(value)

        browser.user_click.side_effect = click
        browser.close.side_effect = browser_closed.set
        applying = asyncio.create_task(session.apply_control(ClickAction(10, 20)))
        await controlling.wait()
        with patch.object(session, "_set_status", side_effect=status):
            with patch.object(session._runtime, "stream", wraps=session._runtime.stream) as stream:
                answering = asyncio.create_task(session.answer("M"))
                await resuming.wait()
                try:
                    async with asyncio.timeout(2):
                        await session.cancel()
                        await answering
                        self.assertEqual(await applying, "Clicked")
                    self.assertIsNot(session._execution_task, previous_execution)
                    self.assertTrue(session._execution_task.done())
                    stream.assert_not_called()
                finally:
                    browser_closed.set()
                    answering.cancel()
                    applying.cancel()
                    await asyncio.gather(answering, applying, return_exceptions=True)

        self.assertEqual(session._status, "cancelled")
        self.assertIsNone(session._capture_task)
        browser.close.assert_awaited_once()

    async def test_cancelling_an_answer_caller_does_not_strand_the_resumed_turn(self):
        call = {"name": "ask_person", "args": {"question": "Which size?"}, "id": str(uuid4())}
        session, browser = build_session(AIMessage(content="", tool_calls=[call]), AIMessage(content="Done"))
        self.addAsyncCleanup(session.close)
        events = session.events()
        await session.start("Buy it")
        await next_event(events, "input.required")
        await session._execution_task
        controlling = asyncio.Event()
        release_control = asyncio.Event()
        resuming = asyncio.Event()
        answered = asyncio.Event()
        caller_alive = asyncio.Event()
        set_status = session._set_status

        async def click(*args):
            controlling.set()
            await release_control.wait()
            return "Clicked"

        async def status(value):
            if value == "running":
                resuming.set()
            await set_status(value)

        async def submit_answer():
            await session.answer("M")
            answered.set()
            await caller_alive.wait()

        browser.user_click.side_effect = click
        applying = asyncio.create_task(session.apply_control(ClickAction(10, 20)))
        await controlling.wait()
        with patch.object(session, "_set_status", side_effect=status):
            answering = asyncio.create_task(submit_answer())
            try:
                async with asyncio.timeout(2):
                    await resuming.wait()
                    answering.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await answering
                    self.assertFalse(session._execution_task.done())
                    release_control.set()
                    self.assertEqual(await applying, "Clicked")
                    self.assertEqual((await next_event(events, "run.completed")).output, "Done")
                    await ready(session, events)
                    await session._execution_task
                self.assertTrue(answered.is_set())
            finally:
                release_control.set()
                answering.cancel()
                await asyncio.gather(answering, applying, return_exceptions=True)

    async def test_concurrent_answers_resume_a_question_once(self):
        call = {"name": "ask_person", "args": {"question": "Which size?"}, "id": str(uuid4())}
        session, _ = build_session(AIMessage(content="", tool_calls=[call]), AIMessage(content="Done"))
        self.addAsyncCleanup(session.close)
        events = session.events()
        await session.start("Buy it")
        await next_event(events, "input.required")
        results = await asyncio.gather(session.answer("M"), session.answer("L"), return_exceptions=True)
        self.assertIsNone(results[0])
        self.assertIsInstance(results[1], SessionNotWaitingForInputError)
        await session._execution_task

    async def test_close_refuses_resume_after_sensitive_submission_finishes(self):
        call = {"name": "request_code", "args": {}, "id": str(uuid4())}
        session, browser = build_session(
            AIMessage(content="", tool_calls=[call]), AIMessage(content="Done"), tools=sensitive_tools,
        )
        self.addAsyncCleanup(session.close)
        events = session.events()
        await session.start("Sign in")
        request = await next_event(events, "sensitive_input.required")
        entered = asyncio.Event()
        proceed = asyncio.Event()
        async def fill(*args):
            entered.set()
            await proceed.wait()
            return "Submitted"
        browser.fill_sensitive.side_effect = fill
        browser.close.side_effect = proceed.set
        submitted = asyncio.create_task(session.submit_sensitive_input(request.request_id, "123456"))
        await entered.wait()
        await session.close()
        with self.assertRaisesRegex(RuntimeError, "already been closed"):
            await submitted
        self.assertTrue(session._closed)
        self.assertTrue(session._execution_task.done())
        self.assertIsNone(session._capture_task)
        browser.close.assert_awaited_once()
