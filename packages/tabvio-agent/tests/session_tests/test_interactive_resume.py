import unittest
from uuid import uuid4

from langchain_core.messages import AIMessage
from tabvio_agent.events import AgentMessageDeltaEvent

from .session_support import build_session, next_event


class SessionInteractiveResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_replayed_input_event_is_suppressed_once(self):
        call = {"name": "ask_person", "args": {"question": "What word should I use?"}, "id": str(uuid4())}
        session, _ = build_session(AIMessage(content="", tool_calls=[call]), AIMessage(content="Hello"))
        self.addAsyncCleanup(session.close)
        events = session.events()
        await session.start("Pick a word")
        self.assertEqual((await next_event(events, "input.required")).question, "What word should I use?")
        self.assertIsNone(await session.answer("Hello"))
        await next_event(events, "input.received")
        received = []
        async for event in events:
            received.append(event.event_type)
            if event.event_type == "run.completed":
                break
        self.assertNotIn("input.required", received)
        self.assertIn("agent.message.delta", received)
        self.assertFalse(session._resuming)

    async def test_a_second_question_is_delivered_after_resume(self):
        first = {"name": "ask_person", "args": {"question": "Which size?"}, "id": str(uuid4())}
        second = {"name": "ask_person", "args": {"question": "Which color?"}, "id": str(uuid4())}
        session, _ = build_session(
            AIMessage(content="", tool_calls=[first]), AIMessage(content="", tool_calls=[second]), AIMessage(content="Bought it"),
        )
        self.addAsyncCleanup(session.close)
        events = session.events()
        await session.start("Buy it")
        await next_event(events, "input.required")
        await session.answer("M")
        self.assertEqual((await next_event(events, "input.required")).question, "Which color?")
        await session.answer("Blue")
        self.assertEqual(await next_event(events, "agent.message.delta"), AgentMessageDeltaEvent("Bought it"))
