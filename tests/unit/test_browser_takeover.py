"""The person watching a parked run driving its browser themselves."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import WebSocketDisconnect

from tabvio.browser.constants import FRAME_QUALITY, FRAME_QUALITY_TAKEOVER
from tabvio.runs import constants
from tabvio.runs.models import BrowserControlEvent, RunContext, RunRecord, RunStatus
from tabvio.runs.repository import RunRepository
from tabvio.runs.sensitive_input import SensitiveInputChannel
from tabvio.runs.service import RunManager
from tabvio.server import routes as app_module
from tests.support import anonymous_client, build_run, signed_in_client


class RecordingBrowser:
    """Stands in for the Playwright session and remembers what it was told."""

    def __init__(self) -> None:
        self.actions: list[tuple] = []
        self.capture_qualities: list[int] = []

    async def user_click(self, horizontal: float, vertical: float) -> str:
        self.actions.append(("click", horizontal, vertical))
        return "Clicked"

    async def user_mouse_down(self, horizontal: float, vertical: float) -> str:
        self.actions.append(("mouse_down", horizontal, vertical))
        return "Held"

    async def user_mouse_up(self) -> str:
        self.actions.append(("mouse_up",))
        return "Released"

    async def user_scroll(
        self, horizontal: float, vertical: float, amount: float
    ) -> str:
        self.actions.append(("scroll", horizontal, vertical, amount))
        return "Scrolled"

    async def user_key(self, key: str) -> str:
        self.actions.append(("key", key))
        return f"Pressed {key}"

    async def user_text(self, text: str) -> str:
        self.actions.append(("text", text))
        return "Typed text"

    async def capture_screen_frame(self, quality: int = FRAME_QUALITY) -> bytes:
        self.capture_qualities.append(quality)
        return b"jpeg-frame"


def build_context(
    run: RunRecord,
    browser: RecordingBrowser,
    pending_element_index: int | None = None,
) -> RunContext:
    channel = SensitiveInputChannel()
    if pending_element_index is not None:
        channel.begin(pending_element_index, "Enter the code")
    return RunContext(
        run=run,
        runtime=SimpleNamespace(
            browser=browser,
            sensitive_inputs=channel,
            resume_input=lambda value: value,
        ),
    )


class TakeoverEndpointTests(unittest.TestCase):
    """The socket itself: who may open it, and what it accepts once open."""

    def setUp(self) -> None:
        # Publishing a takeover event would otherwise reach the real database.
        saved_events = patch.object(app_module.repository, "save_event")
        saved_events.start()
        self.addCleanup(saved_events.stop)

    def connect(self, client, run: RunRecord):
        return client.websocket_connect(f"/api/runs/{run.id}/control")

    def open_run(
        self,
        user_id,
        status=RunStatus.WAITING_FOR_INPUT,
        pending_element_index=None,
    ):
        run = build_run(user_id, status=status)
        browser = RecordingBrowser()
        app_module.run_manager._contexts[run.id] = build_context(
            run, browser, pending_element_index
        )
        self.addCleanup(app_module.run_manager._contexts.pop, run.id, None)
        return run, browser

    def test_a_person_can_click_type_and_scroll_the_agents_browser(self) -> None:
        with signed_in_client() as (client, user):
            run, browser = self.open_run(user.id)
            with self.connect(client, run) as socket:
                socket.send_json({"type": "click", "x": 100, "y": 200})
                click_result = socket.receive_json()
                socket.send_json({"type": "text", "text": "hello"})
                socket.receive_json()
                socket.send_json({"type": "key", "key": "Enter"})
                socket.receive_json()
                socket.send_json({"type": "scroll", "x": 10, "y": 20, "delta_y": 300})
                socket.receive_json()

        self.assertTrue(click_result["applied"])
        self.assertEqual(
            browser.actions,
            [
                ("click", 100.0, 200.0),
                ("text", "hello"),
                ("key", "Enter"),
                ("scroll", 10.0, 20.0, 300.0),
            ],
        )

    def test_takeover_is_refused_while_the_agent_is_working(self) -> None:
        with signed_in_client() as (client, user):
            run, browser = self.open_run(user.id, status=RunStatus.RUNNING)
            with self.assertRaises(WebSocketDisconnect) as refusal:
                with self.connect(client, run):
                    pass

        self.assertEqual(refusal.exception.code, app_module.CONTROL_CLOSE_RUN_NOT_WAITING)
        self.assertEqual(browser.actions, [])

    def test_mouse_stays_down_until_explicit_release(self) -> None:
        with signed_in_client() as (client, user):
            run, browser = self.open_run(user.id)
            with self.connect(client, run) as socket:
                socket.send_json({"type": "mouse_down", "x": 100, "y": 200})
                self.assertTrue(socket.receive_json()["applied"])
                self.assertEqual(browser.actions, [("mouse_down", 100.0, 200.0)])
                socket.send_json({"type": "mouse_up"})
                self.assertTrue(socket.receive_json()["applied"])
        self.assertEqual(browser.actions, [("mouse_down", 100.0, 200.0), ("mouse_up",)])

    def test_disconnect_releases_mouse_even_with_another_controller_connected(self) -> None:
        with signed_in_client() as (client, user):
            run, browser = self.open_run(user.id)
            with self.connect(client, run) as other:
                with self.connect(client, run) as owner:
                    owner.send_json({"type": "mouse_down", "x": 100, "y": 200})
                    self.assertTrue(owner.receive_json()["applied"])
                    other.send_json({"type": "mouse_up"})
                    other.receive_json()
                    self.assertEqual(browser.actions, [("mouse_down", 100.0, 200.0)])
                other.send_json({"type": "mouse_down", "x": 10, "y": 20})
                self.assertTrue(other.receive_json()["applied"])
        self.assertEqual(browser.actions, [
            ("mouse_down", 100.0, 200.0), ("mouse_up",),
            ("mouse_down", 10.0, 20.0), ("mouse_up",),
        ])

    def test_follow_up_window_allows_control_and_restarts_live_capture(self) -> None:
        with signed_in_client() as (client, user):
            run, browser = self.open_run(user.id, status=RunStatus.READY_FOR_FOLLOW_UP)
            context = app_module.run_manager._contexts[run.id]
            with self.connect(client, run) as socket:
                socket.send_json({"type": "click", "x": 100, "y": 200})
                self.assertTrue(socket.receive_json()["applied"])
                self.assertIsNotNone(context.capture_task)
                self.assertFalse(context.capture_task.done())
        self.assertEqual(browser.actions, [("click", 100.0, 200.0)])
        self.assertIsNone(context.capture_task)

    def test_existing_controller_cannot_act_after_agent_resumes_or_session_ends(self) -> None:
        for status in (RunStatus.RUNNING, RunStatus.SUCCEEDED):
            with self.subTest(status=status), signed_in_client() as (client, user):
                run, browser = self.open_run(user.id)
                with self.connect(client, run) as socket:
                    run.status = status
                    socket.send_json({"type": "click", "x": 100, "y": 200})
                    self.assertFalse(socket.receive_json()["applied"])
                self.assertEqual(browser.actions, [])

    def test_taking_control_drops_a_pending_verification_code(self) -> None:
        """That request holds an element index a person clicking around would break."""
        resumed = patch.object(app_module.run_manager, "_execute", new=AsyncMock())
        resumed.start()
        self.addCleanup(resumed.stop)

        with signed_in_client() as (client, user):
            run, browser = self.open_run(user.id, pending_element_index=3)
            context = app_module.run_manager._contexts[run.id]
            with self.connect(client, run) as socket:
                self.assertIsNone(context.runtime.sensitive_inputs.pending)
                socket.send_json({"type": "click", "x": 100, "y": 200})
                self.assertTrue(socket.receive_json()["applied"])

        self.assertEqual(browser.actions, [("click", 100.0, 200.0)])
        resume_value = app_module.run_manager._execute.await_args.args[1]
        self.assertEqual(resume_value["entered"], False)
        self.assertEqual(
            resume_value["reason"],
            constants.SENSITIVE_INPUT_TAKEOVER_REASON,
        )

    def test_another_accounts_browser_cannot_be_driven(self) -> None:
        with signed_in_client() as (client, _):
            run, browser = self.open_run(uuid4())
            with self.assertRaises(WebSocketDisconnect) as refusal:
                with self.connect(client, run):
                    pass

        self.assertEqual(refusal.exception.code, app_module.CONTROL_CLOSE_RUN_NOT_FOUND)
        self.assertEqual(browser.actions, [])

    def test_signing_in_is_required(self) -> None:
        run = build_run(uuid4(), status=RunStatus.WAITING_FOR_INPUT)
        with anonymous_client() as client:
            with self.assertRaises(WebSocketDisconnect) as refusal:
                with self.connect(client, run):
                    pass

        self.assertEqual(
            refusal.exception.code, app_module.CONTROL_CLOSE_UNAUTHENTICATED
        )

    def test_unusable_events_are_reported_and_never_reach_the_browser(self) -> None:
        with signed_in_client() as (client, user):
            run, browser = self.open_run(user.id)
            with self.connect(client, run) as socket:
                # Outside the viewport, a key that is not on the allowlist, and
                # a type the socket does not know.
                socket.send_json({"type": "click", "x": 99_999, "y": 10})
                outside_viewport = socket.receive_json()
                socket.send_json({"type": "key", "key": "F12"})
                unlisted_key = socket.receive_json()
                socket.send_json({"type": "drag", "x": 1, "y": 1})
                unknown_type = socket.receive_json()

        for acknowledgement in (outside_viewport, unlisted_key, unknown_type):
            self.assertFalse(acknowledgement["applied"])
        self.assertEqual(browser.actions, [])


class TakeoverCaptureTests(unittest.IsolatedAsyncioTestCase):
    """Frames while somebody is driving: faster, and readable enough to click."""

    INTERVAL_SECONDS = 0.01

    async def asyncSetUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        repository = RunRepository(Path(self._directory.name) / "tabvio.db")
        repository.initialize()
        self._manager = RunManager(repository)
        for name in ("FRAME_INTERVAL_SECONDS", "FRAME_INTERVAL_SECONDS_TAKEOVER"):
            interval = patch.object(constants, name, self.INTERVAL_SECONDS)
            interval.start()
            self.addCleanup(interval.stop)

    async def _capture_one_frame(self, context: RunContext) -> None:
        capture = asyncio.create_task(self._manager._capture_frames(context))
        while context.latest_frame is None:
            await asyncio.sleep(self.INTERVAL_SECONDS)
        context.run.status = RunStatus.SUCCEEDED
        await capture

    async def test_resuming_or_ending_run_releases_held_mouse(self) -> None:
        for status in (RunStatus.RUNNING, RunStatus.SUCCEEDED, RunStatus.CANCELLED):
            with self.subTest(status=status):
                browser = RecordingBrowser()
                run = RunRecord(task="Hold", max_runtime_seconds=300, user_id=uuid4(),
                                status=RunStatus.WAITING_FOR_INPUT)
                context = build_context(run, browser)
                await self._manager.apply_control(
                    context, BrowserControlEvent(type="mouse_down", x=10, y=20)
                )
                await self._manager._set_status(context, status)
                self.assertEqual(browser.actions, [("mouse_down", 10.0, 20.0), ("mouse_up",)])
                self.assertIsNone(context.mouse_controller)

    async def test_frames_are_captured_at_a_higher_quality_during_takeover(self) -> None:
        browser = RecordingBrowser()
        run = RunRecord(task="Take over", max_runtime_seconds=300, user_id=uuid4())
        watched = build_context(run, browser)
        await self._capture_one_frame(watched)
        self.assertEqual(browser.capture_qualities[0], FRAME_QUALITY)

        driven_browser = RecordingBrowser()
        driven_run = RunRecord(task="Take over", max_runtime_seconds=300, user_id=uuid4())
        driven = build_context(driven_run, driven_browser)
        driven.controller_count = 1
        await self._capture_one_frame(driven)
        self.assertEqual(driven_browser.capture_qualities[0], FRAME_QUALITY_TAKEOVER)


if __name__ == "__main__":
    unittest.main()
