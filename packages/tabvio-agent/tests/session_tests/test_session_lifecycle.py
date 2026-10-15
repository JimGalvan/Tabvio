import asyncio
import unittest
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from tabvio_agent.exceptions import SessionNotWaitingForInputError

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

    async def test_close_waits_for_sensitive_submission_and_stops_the_resumed_turn(self):
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
        submitted = asyncio.create_task(session.submit_sensitive_input(request.request_id, "123456"))
        await entered.wait()
        closing = asyncio.create_task(session.close())
        proceed.set()
        await asyncio.gather(submitted, closing)
        self.assertTrue(session._closed)
        self.assertTrue(session._execution_task.done())
        self.assertIsNone(session._capture_task)
        browser.close.assert_awaited_once()
