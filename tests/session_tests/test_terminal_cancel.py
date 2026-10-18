import unittest

from langchain_core.messages import AIMessage

from .session_support import build_session, ready


class SessionCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_a_closed_session_is_idempotent(self):
        session, browser = build_session(AIMessage(content="Done"))
        await session.start("Go")
        await ready(session, session.events())
        await session.close()
        self.assertIsNone(await session.cancel())
        self.assertIsNone(await session.cancel())
        browser.close.assert_awaited_once()

    async def test_cancelling_an_idle_follow_up_emits_once_and_closes_browser(self):
        session, browser = build_session(AIMessage(content="Done"))
        events = session.events()
        await session.start("Go")
        await ready(session, events)
        await session.cancel()
        await session.cancel()
        remaining = []
        async for event in events:
            remaining.append(event.event_type)
        self.assertEqual(remaining, ["run.cancelled", "run.status"])
        browser.close.assert_awaited_once()

    async def test_immediate_cancel_stops_the_queued_execution(self):
        session, browser = build_session(AIMessage(content="Done"))
        await session.start("Go")
        await session.cancel()
        collected = []
        async for event in session.events():
            collected.append(event.event_type)
        self.assertEqual(collected, ["run.status", "run.cancelled", "run.status"])
        browser.close.assert_awaited_once()
