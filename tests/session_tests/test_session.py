import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage

from tabvio_agent import BrowserFrameChangedEvent
from tabvio_agent.events import AgentMessageDeltaEvent, RunCompletedEvent
from tabvio_agent.exceptions import (
    SessionNotReadyForFollowUpError,
    SessionNotWaitingForInputError,
)
from tabvio_agent.settings import AgentSettings

from .session_support import build_session, next_event, ready


class SessionCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_commands_return_none_and_results_are_typed_events(self):
        session, browser = build_session(AIMessage(content="Done."))
        self.addAsyncCleanup(session.close)
        events = session.events()
        self.assertIsNone(await session.start("Read the page"))
        delta = await next_event(events, "agent.message.delta")
        self.assertEqual(delta, AgentMessageDeltaEvent("Done."))
        with self.assertRaises(FrozenInstanceError):
            delta.text = "changed"
        completed = await next_event(events, "run.completed")
        self.assertEqual(completed, RunCompletedEvent("Done."))
        await ready(session, events)
        browser.close.assert_not_awaited()

    async def test_frame_custom_event_becomes_a_frozen_typed_event(self):
        session, _ = build_session()
        self.addAsyncCleanup(session.close)
        await session._handle_stream_item({
            "kind": "custom", "event_type": "browser.frame.changed",
            "payload": {"frame_id": "frame:1"},
        })

        event = await anext(session.events())

        self.assertEqual(event, BrowserFrameChangedEvent("frame:1"))
        self.assertEqual(event.event_type, "browser.frame.changed")
        with self.assertRaises(FrozenInstanceError):
            event.frame_id = "frame:0"

    async def test_follow_up_reuses_the_browser_and_thread(self):
        session, browser = build_session(AIMessage(content="First"), AIMessage(content="Second"))
        self.addAsyncCleanup(session.close)
        events = session.events()
        thread = session._runtime.config
        await session.start("First task")
        await ready(session, events)
        self.assertIsNone(await session.follow_up("Second task"))
        self.assertEqual(await next_event(events, "run.completed"), RunCompletedEvent("Second"))
        self.assertIs(session._runtime.config, thread)
        self.assertIs(session._runtime.browser, browser)
        messages = (await session._runtime.agent.aget_state(thread)).values["messages"]
        tasks = []
        for message in messages:
            if message.type == "human":
                tasks.append(message.content)
        self.assertEqual(tasks, ["First task", "Second task"])

    async def test_final_output_excludes_prior_narration(self):
        from .session_support import ask_person
        call = {"name": ask_person.name, "args": {"question": "Which size?"}, "id": str(uuid4())}
        session, _ = build_session(
            AIMessage(content="Let me check.", tool_calls=[call]), AIMessage(content="Bought M."),
        )
        self.addAsyncCleanup(session.close)
        events = session.events()
        await session.start("Buy a shirt")
        await next_event(events, "input.required")
        await session.answer("M")
        self.assertEqual(await next_event(events, "run.completed"), RunCompletedEvent("Bought M."))

    async def test_stream_failure_emits_error_and_closes_browser(self):
        session, browser = build_session()
        async def failing_stream(value):
            raise RuntimeError("model died")
            yield
        session._runtime.stream = failing_stream
        events = session.events()
        await session.start("Go")
        error = await next_event(events, "run.failed")
        self.assertEqual(error.error, "model died")
        await session._execution_task
        browser.close.assert_awaited_once()
        remaining = []
        async for event in events:
            remaining.append(event.event_type)
        self.assertEqual(remaining, ["run.status"])

    async def test_empty_final_output_uses_streamed_text(self):
        session, _ = build_session(AIMessage(content="Fallback"))
        self.addAsyncCleanup(session.close)
        events = session.events()
        with patch.object(session._runtime, "final_output", return_value=""):
            await session.start("Go")
            self.assertEqual(await next_event(events, "run.completed"), RunCompletedEvent("Fallback"))

    async def test_answer_and_follow_up_require_their_respective_states(self):
        session, _ = build_session()
        self.addAsyncCleanup(session.close)
        with self.assertRaises(SessionNotWaitingForInputError):
            await session.answer("M")
        with self.assertRaises(SessionNotReadyForFollowUpError):
            await session.follow_up("Next")

    async def test_start_is_allowed_once(self):
        session, _ = build_session(AIMessage(content="Done"))
        self.addAsyncCleanup(session.close)
        await session.start("Go")
        with self.assertRaises(RuntimeError):
            await session.start("Again")

    async def test_context_manager_closes_the_browser(self):
        session, browser = build_session(AIMessage(content="Done"))
        async with session as entered:
            self.assertIs(entered, session)
            await session.start("Go")
        browser.close.assert_awaited_once()

    async def test_execution_limit_emits_original_timeout_error(self):
        import asyncio
        session, browser = build_session(settings=AgentSettings(max_runtime_seconds=0.01))
        async def slow_stream(value):
            await asyncio.sleep(1)
            yield
        session._runtime.stream = slow_stream
        events = session.events()
        await session.start("Go")
        self.assertEqual((await next_event(events, "run.failed")).error, "The run exceeded its execution time limit")
        self.assertEqual((await next_event(events, "run.status")).status, "timed_out")
        await session._execution_task
        browser.close.assert_awaited_once()
