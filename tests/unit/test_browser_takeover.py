import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import WebSocketDisconnect

from tabvio.runs.models import BrowserControlEvent, RunContext, RunStatus
from tabvio.server import routes as app_module
from tests.support import anonymous_client, build_run, signed_in_client


class TakeoverEndpointTests(unittest.TestCase):
    def setUp(self):
        self.session = AsyncMock()
        self.apply = AsyncMock(return_value="Clicked")
        patcher = patch.object(app_module.run_manager, "apply_control", self.apply)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_run(self, user_id, status=RunStatus.WAITING_FOR_INPUT):
        run = build_run(user_id, status=status)
        context = RunContext(run=run, session=self.session)
        app_module.run_manager._contexts[run.id] = context
        self.addCleanup(app_module.run_manager._contexts.pop, run.id, None)
        return run, context

    def test_socket_dispatches_validated_actions_and_acknowledges_results(self):
        with signed_in_client() as (client, user):
            run, context = self.open_run(user.id)
            with client.websocket_connect(f"/api/runs/{run.id}/control") as socket:
                socket.send_json({"type": "click", "x": 100, "y": 200})
                self.assertEqual(socket.receive_json(), {"applied": True, "detail": "Clicked"})
        self.session.take_control.assert_awaited_once()
        self.session.release_control.assert_awaited_once()
        arguments = self.apply.await_args.args
        self.assertIs(arguments[0], context)
        self.assertEqual(arguments[1], BrowserControlEvent(type="click", x=100, y=200))

    def test_takeover_is_refused_while_the_agent_is_working(self):
        with signed_in_client() as (client, user):
            run, _ = self.open_run(user.id, RunStatus.RUNNING)
            with self.assertRaises(WebSocketDisconnect) as refusal:
                with client.websocket_connect(f"/api/runs/{run.id}/control"):
                    pass
        self.assertEqual(refusal.exception.code, app_module.CONTROL_CLOSE_RUN_NOT_WAITING)
        self.session.take_control.assert_not_awaited()

    def test_another_accounts_browser_cannot_be_driven(self):
        with signed_in_client() as (client, _):
            run, _ = self.open_run(uuid4())
            with self.assertRaises(WebSocketDisconnect) as refusal:
                with client.websocket_connect(f"/api/runs/{run.id}/control"):
                    pass
        self.assertEqual(refusal.exception.code, app_module.CONTROL_CLOSE_RUN_NOT_FOUND)
        self.session.take_control.assert_not_awaited()

    def test_signing_in_is_required(self):
        run = build_run(uuid4(), status=RunStatus.WAITING_FOR_INPUT)
        with anonymous_client() as client:
            with self.assertRaises(WebSocketDisconnect) as refusal:
                with client.websocket_connect(f"/api/runs/{run.id}/control"):
                    pass
        self.assertEqual(refusal.exception.code, app_module.CONTROL_CLOSE_UNAUTHENTICATED)

    def test_unusable_events_are_reported_and_never_reach_the_session(self):
        with signed_in_client() as (client, user):
            run, _ = self.open_run(user.id)
            with client.websocket_connect(f"/api/runs/{run.id}/control") as socket:
                for event in (
                    {"type": "click", "x": 99_999, "y": 10},
                    {"type": "key", "key": "F12"}, {"type": "drag", "x": 1, "y": 1},
                ):
                    socket.send_json(event)
                    self.assertFalse(socket.receive_json()["applied"])
        self.apply.assert_not_awaited()

    def test_session_errors_are_reported_to_the_socket(self):
        self.apply.side_effect = RuntimeError("The browser is not available for user control")
        with signed_in_client() as (client, user):
            run, _ = self.open_run(user.id)
            with client.websocket_connect(f"/api/runs/{run.id}/control") as socket:
                socket.send_json({"type": "click", "x": 100, "y": 200})
                self.assertEqual(socket.receive_json(), {
                    "applied": False, "detail": "The browser is not available for user control",
                })


class TakeoverControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_releases_only_the_mouse_held_by_that_controller(self):
        session = AsyncMock()
        run = build_run(uuid4(), status=RunStatus.WAITING_FOR_INPUT)
        context = RunContext(run=run, session=session)
        async def applied(action):
            await context.control_results.put("Applied")
        session.apply_control.side_effect = applied
        manager = app_module.run_manager
        await manager.apply_control(context, BrowserControlEvent(type="mouse_down", x=100, y=200), "owner")
        await manager.apply_control(context, BrowserControlEvent(type="mouse_up"), "other")
        self.assertEqual(session.apply_control.await_count, 1)
        await manager.close_control(context, "owner")
        self.assertEqual(session.apply_control.await_count, 2)
        self.assertEqual(session.apply_control.await_args.args[0].type, "mouse_up")
        self.assertIsNone(context.mouse_controller)
        session.release_control.assert_awaited_once()

    async def test_existing_controller_cannot_act_after_agent_resumes_or_run_ends(self):
        for status in (RunStatus.RUNNING, RunStatus.SUCCEEDED):
            with self.subTest(status=status):
                session = AsyncMock()
                context = RunContext(run=build_run(uuid4(), status=status), session=session)
                from tabvio.runs.exceptions import RunNotWaitingForInputError
                with self.assertRaises(RunNotWaitingForInputError):
                    await app_module.run_manager.apply_control(context, BrowserControlEvent(type="click", x=1, y=2))
                session.apply_control.assert_not_awaited()
