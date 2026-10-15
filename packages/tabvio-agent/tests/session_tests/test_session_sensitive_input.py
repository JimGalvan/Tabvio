import asyncio
import unittest
from dataclasses import asdict
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.types import interrupt
from tabvio_agent import constants
from tabvio_agent.exceptions import (
    SensitiveInputNotPendingError,
    SessionNotWaitingForInputError,
)
from tabvio_agent.settings import AgentSettings
from tabvio_agent.shared.events import publish_custom_event

from .session_support import build_session, next_event, ready


def sensitive_tools(browser, channel):
    @tool
    def request_code(submit_element_index: int | None = None) -> str:
        """Request a verification code and wait for the person to enter it."""
        pending = channel.begin(8, "Enter the code", submit_element_index)
        publish_custom_event("sensitive_input.required", {
            "request_id": str(pending.id), "kind": pending.kind, "prompt": pending.prompt,
        })
        result = interrupt({"request_id": str(pending.id)})
        channel.clear(pending.id)
        return str(result)
    return [request_code]


class SessionSensitiveInputTests(unittest.IsolatedAsyncioTestCase):
    async def park(self, submit_element_index=None, window=240):
        call = {
            "name": "request_code", "args": {"submit_element_index": submit_element_index}, "id": str(uuid4()),
        }
        session, browser = build_session(
            AIMessage(content="", tool_calls=[call]), AIMessage(content="Done"),
            tools=sensitive_tools, settings=AgentSettings(sensitive_input_window_seconds=window),
        )
        self.addAsyncCleanup(session.close)
        events = session.events()
        await session.start("Sign in")
        request = await next_event(events, "sensitive_input.required")
        return session, browser, events, request

    async def test_code_is_filled_but_never_emitted_or_sent_to_the_model(self):
        session, browser, events, request = await self.park()
        with patch.object(session._runtime, "resume_input", wraps=session._runtime.resume_input) as resume:
            self.assertIsNone(await session.submit_sensitive_input(request.request_id, "123456"))
            received = await next_event(events, "sensitive_input.received")
            self.assertEqual(received.request_id, request.request_id)
            self.assertNotIn("123456", str(asdict(received)))
            await ready(session, events)
            self.assertNotIn("123456", str(resume.call_args))
            self.assertTrue(resume.call_args.args[0]["entered"])
        browser.fill_sensitive.assert_awaited_once_with(8, "123456", None)
        state = await session._runtime.agent.aget_state(session._runtime.config)
        self.assertNotIn("123456", str(state.values))

    async def test_the_code_is_sent_rather_than_left_in_the_field(self):
        session, browser, events, request = await self.park(submit_element_index=12)
        with patch.object(session._runtime, "resume_input", wraps=session._runtime.resume_input) as resume:
            await session.submit_sensitive_input(request.request_id, "123456")
            await ready(session, events)
        browser.fill_sensitive.assert_awaited_once_with(8, "123456", 12)
        self.assertEqual(resume.call_args.args[0], {
            "entered": True, "submitted": "Filled element [8] and submitted it",
        })

    async def test_cancel_closes_the_browser_during_sensitive_submission(self):
        session, browser, events, request = await self.park(submit_element_index=12)
        entered = asyncio.Event()
        browser_closed = asyncio.Event()

        async def fill(*args):
            entered.set()
            await browser_closed.wait()
            raise RuntimeError("Browser closed before submission")

        browser.fill_sensitive.side_effect = fill
        browser.close.side_effect = browser_closed.set
        submitting = asyncio.create_task(session.submit_sensitive_input(request.request_id, "123456"))
        await entered.wait()
        try:
            async with asyncio.timeout(2):
                await session.cancel()
            with self.assertRaisesRegex(RuntimeError, "Browser closed before submission"):
                await submitting
        finally:
            submitting.cancel()
            await asyncio.gather(submitting, return_exceptions=True)

        browser.close.assert_awaited_once()
        self.assertTrue(session._execution_task.done())
        remaining = []
        async for event in events:
            remaining.append(event.event_type)
        self.assertEqual(remaining, ["run.status", "run.cancelled", "run.status"])
        self.assertEqual(session._status, "cancelled")

    async def test_declining_sends_the_agent_back_without_a_code(self):
        session, browser, events, request = await self.park()
        with patch.object(session._runtime, "resume_input", wraps=session._runtime.resume_input) as resume:
            self.assertIsNone(await session.decline_sensitive_input(request.request_id))
            cancelled = await next_event(events, "sensitive_input.cancelled")
            self.assertEqual(cancelled.request_id, request.request_id)
            self.assertEqual(cancelled.reason, constants.SENSITIVE_INPUT_DECLINED_REASON)
            await ready(session, events)
        browser.fill_sensitive.assert_not_awaited()
        self.assertEqual(resume.call_args.args[0], {"entered": False, "reason": constants.SENSITIVE_INPUT_DECLINED_REASON})

    async def test_declining_a_dropped_request_is_refused(self):
        session, _, _, request = await self.park()
        await session.decline_sensitive_input(request.request_id)
        with self.assertRaises(SensitiveInputNotPendingError):
            await session.decline_sensitive_input(request.request_id)

    async def test_a_submitted_request_cannot_be_declined_while_resuming(self):
        session, _, events, request = await self.park()
        await session.submit_sensitive_input(request.request_id, "123456")
        with self.assertRaises(SensitiveInputNotPendingError):
            await session.decline_sensitive_input(request.request_id)
        await ready(session, events)
        await session._execution_task
        self.assertTrue(session._event_queue.empty())

    async def test_an_unanswered_code_request_expires(self):
        session, _, events, request = await self.park(window=0.02)
        with patch.object(session._runtime, "resume_input", wraps=session._runtime.resume_input) as resume:
            cancelled = await next_event(events, "sensitive_input.cancelled")
            self.assertEqual(cancelled.reason, constants.SENSITIVE_INPUT_TIMEOUT_REASON)
            await ready(session, events)
        self.assertEqual(resume.call_args.args[0], {"entered": False, "reason": constants.SENSITIVE_INPUT_TIMEOUT_REASON})
        self.assertIsNone(session._sensitive_inputs.pending)

    async def test_entering_a_code_stops_the_clock(self):
        session, browser, events, request = await self.park(window=0.02)
        await session.submit_sensitive_input(request.request_id, "123456")
        await ready(session, events)
        await asyncio.sleep(0.05)
        self.assertIsNone(session._sensitive_input_timeout_task)
        self.assertTrue(session._event_queue.empty())
        browser.fill_sensitive.assert_awaited_once()

    async def test_the_dashboard_is_told_when_the_code_window_closes(self):
        before = datetime.now(UTC)
        _, _, _, request = await self.park()
        self.assertGreater(request.expires_at, before)
        self.assertEqual(request.kind, "mfa_code")
        self.assertEqual(request.prompt, "Enter the code")

    async def test_stale_request_ids_are_refused(self):
        session, browser, _, _ = await self.park()
        with self.assertRaisesRegex(SensitiveInputNotPendingError, "no longer active"):
            await session.submit_sensitive_input(uuid4(), "123456")
        with self.assertRaisesRegex(SensitiveInputNotPendingError, "no longer active"):
            await session.decline_sensitive_input(uuid4())
        browser.fill_sensitive.assert_not_awaited()

    async def test_a_regular_answer_cannot_replace_a_verification_code(self):
        session, _, _, _ = await self.park()
        with self.assertRaisesRegex(SessionNotWaitingForInputError, "verification code"):
            await session.answer("123456")
