import unittest
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage

from tabvio_agent import constants
from tabvio_agent.browser.constants import FRAME_QUALITY, FRAME_QUALITY_TAKEOVER
from tabvio_agent.exceptions import SessionNotWaitingForInputError
from tabvio_agent.models import (
    ClickAction,
    KeyAction,
    MouseDownAction,
    MouseUpAction,
    ScrollAction,
    TextAction,
)

from .session_support import build_session, next_event, ready
from .test_session_sensitive_input import sensitive_tools


class SessionTakeoverTests(unittest.IsolatedAsyncioTestCase):
    async def park(self, sensitive=False):
        if sensitive:
            call = {"name": "request_code", "args": {}, "id": str(uuid4())}
            tools = sensitive_tools
        else:
            call = {"name": "ask_person", "args": {"question": "Continue?"}, "id": str(uuid4())}
            tools = None
        session, browser = build_session(
            AIMessage(content="", tool_calls=[call]), AIMessage(content="Done"), AIMessage(content="Followed up"), tools=tools,
        )
        self.addAsyncCleanup(session.close)
        events = session.events()
        await session.start("Open the page")
        await next_event(events, "sensitive_input.required" if sensitive else "input.required")
        return session, browser, events

    async def test_a_person_can_click_type_and_scroll_the_browser(self):
        session, browser, events = await self.park()
        self.assertIsNone(await session.take_control())
        await next_event(events, "takeover.started")
        for action, detail in (
            (ClickAction(100, 200), "Clicked"), (TextAction("hello"), "Typed text"),
            (KeyAction("Enter"), "Pressed Enter"), (ScrollAction(10, 20, 300), "Scrolled"),
        ):
            self.assertEqual(await session.apply_control(action), detail)
            self.assertEqual((await next_event(events, "browser.control.applied")).detail, detail)
        browser.user_click.assert_awaited_once_with(100, 200)
        browser.user_text.assert_awaited_once_with("hello")
        browser.user_key.assert_awaited_once_with("Enter")
        browser.user_scroll.assert_awaited_once_with(10, 20, 300)

    async def test_mouse_stays_down_until_explicit_release(self):
        session, browser, _ = await self.park()
        await session.take_control()
        await session.apply_control(MouseDownAction(100, 200))
        browser.user_mouse_up.assert_not_awaited()
        await session.apply_control(MouseUpAction())
        browser.user_mouse_up.assert_awaited_once()

    async def test_other_pointer_actions_require_releasing_a_held_mouse(self):
        session, _, _ = await self.park()
        await session.apply_control(MouseDownAction(100, 200))
        for action in (ClickAction(1, 2), MouseDownAction(1, 2), ScrollAction(1, 2, 3)):
            with self.assertRaisesRegex(SessionNotWaitingForInputError, "Release the held mouse button"):
                await session.apply_control(action)

    async def test_releasing_the_last_controller_releases_the_mouse(self):
        session, browser, events = await self.park()
        await session.take_control()
        await session.take_control()
        await session.apply_control(MouseDownAction(100, 200))
        await session.release_control()
        browser.user_mouse_up.assert_not_awaited()
        await session.release_control()
        browser.user_mouse_up.assert_awaited_once()
        await next_event(events, "takeover.ended")

    async def test_resuming_or_ending_releases_the_held_mouse(self):
        for command in ("answer", "cancel", "close"):
            with self.subTest(command=command):
                session, browser, _ = await self.park()
                await session.apply_control(MouseDownAction(10, 20))
                if command == "answer":
                    await session.answer("Yes")
                else:
                    await getattr(session, command)()
                browser.user_mouse_up.assert_awaited_once()
                self.assertFalse(session._mouse_down)

    async def test_follow_up_window_allows_control_and_restarts_live_capture(self):
        session, _, events = await self.park()
        await session.answer("Yes")
        await ready(session, events)
        await session._execution_task
        self.assertIsNone(session._capture_task)
        await session.take_control()
        self.assertIsNotNone(session._capture_task)
        await session.apply_control(ClickAction(1, 2))
        await session.release_control()
        self.assertIsNone(session._capture_task)

    async def test_taking_control_drops_a_pending_code_and_resumes_on_release(self):
        session, browser, events = await self.park(sensitive=True)
        with patch.object(session._runtime, "resume_input", wraps=session._runtime.resume_input) as resume:
            await session.take_control()
            cancelled = await next_event(events, "sensitive_input.cancelled")
            self.assertEqual(cancelled.reason, constants.SENSITIVE_INPUT_TAKEOVER_REASON)
            self.assertIsNone(session._sensitive_inputs.pending)
            resume.assert_not_called()
            await session.apply_control(ClickAction(100, 200))
            await session.release_control()
            await ready(session, events)
        browser.fill_sensitive.assert_not_awaited()
        self.assertEqual(resume.call_args.args[0], {"entered": False, "reason": constants.SENSITIVE_INPUT_TAKEOVER_REASON})

    async def test_frames_are_captured_at_a_higher_quality_during_takeover(self):
        session, browser, _ = await self.park()
        frames = session.screen_frames()
        await anext(frames)
        browser.capture_screen_frame.assert_awaited_with(FRAME_QUALITY)
        await session._pause_frame_capture()
        await session.take_control()
        await anext(frames)
        browser.capture_screen_frame.assert_awaited_with(FRAME_QUALITY_TAKEOVER)
        await frames.aclose()

    async def test_existing_controller_cannot_act_after_the_session_ends(self):
        session, browser, _ = await self.park()
        await session.take_control()
        await session.close()
        with self.assertRaisesRegex(SessionNotWaitingForInputError, "not available"):
            await session.apply_control(ClickAction(100, 200))
        browser.user_click.assert_not_awaited()

    async def test_follow_up_releases_a_held_mouse(self):
        session, browser, events = await self.park()
        await session.answer("Yes")
        await ready(session, events)
        await session.apply_control(MouseDownAction(10, 20))
        await session.follow_up("Continue")
        browser.user_mouse_up.assert_awaited_once()

    async def test_a_failed_mouse_down_releases_the_mouse(self):
        session, browser, _ = await self.park()
        browser.user_mouse_down.side_effect = RuntimeError("mouse failed")
        with self.assertRaisesRegex(RuntimeError, "mouse failed"):
            await session.apply_control(MouseDownAction(1, 2))
        browser.user_mouse_up.assert_awaited_once()
