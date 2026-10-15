import unittest
from unittest.mock import patch

from tabvio_agent import constants

from .session_support import FakeBrowser, build_session


class RecoveringBrowser(FakeBrowser):
    def __init__(self):
        super().__init__()
        self.capture_screen_frame.side_effect = [RuntimeError("temporary screenshot failure"), b"jpeg-frame"]


class SessionFrameCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for name, value in (
            ("FRAME_RETRY_INTERVAL_SECONDS", 0.01), ("FRAME_INTERVAL_SECONDS", 0.01),
            ("FRAME_CAPTURE_GRACE_SECONDS", 0.0),
        ):
            patcher = patch.object(constants, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def capture(self):
        browser = RecoveringBrowser()
        session, _ = build_session(browser=browser)
        self.addAsyncCleanup(session.close)
        session._start_frame_capture()
        frames = session.screen_frames()
        frame = await anext(frames)
        await frames.aclose()
        await session._pause_frame_capture()
        await session.close()
        events = []
        async for event in session.events():
            events.append(event.event_type)
        return session, browser, frame, events

    async def test_an_outage_past_the_grace_period_is_announced_and_recovered(self):
        session, browser, frame, events = await self.capture()
        self.assertEqual(frame, b"jpeg-frame")
        self.assertEqual(session.get_latest_frame(), frame)
        self.assertEqual(browser.capture_screen_frame.await_count, 2)
        self.assertEqual(events, ["browser.capture.failed", "browser.capture.recovered"])

    async def test_a_blip_inside_the_grace_period_is_not_announced(self):
        with patch.object(constants, "FRAME_CAPTURE_GRACE_SECONDS", 60.0):
            session, browser, frame, events = await self.capture()
        self.assertEqual(session.get_latest_frame(), b"jpeg-frame")
        self.assertEqual(events, [])

    async def test_capture_calls_have_the_original_timeout(self):
        session, _ = build_session()
        self.addAsyncCleanup(session.close)
        import asyncio
        with patch("tabvio_agent.session.asyncio.wait_for", wraps=asyncio.wait_for) as capture:
            session._start_frame_capture()
            frames = session.screen_frames()
            self.assertEqual(await anext(frames), b"jpeg-frame")
            await frames.aclose()
            self.assertEqual(capture.call_args.kwargs["timeout"], constants.FRAME_CAPTURE_TIMEOUT_SECONDS)

    async def test_closed_frame_stream_delivers_the_last_frame_and_ends(self):
        session, _ = build_session()
        session._start_frame_capture()
        frames = session.screen_frames()
        self.assertEqual(await anext(frames), b"jpeg-frame")
        await session.close()
        with self.assertRaises(StopAsyncIteration):
            await anext(frames)
        cached = session.screen_frames()
        self.assertEqual(await anext(cached), b"jpeg-frame")
        with self.assertRaises(StopAsyncIteration):
            await anext(cached)
