import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from tabvio.auth.models import User
from tabvio.runs.models import RunRecord
from tabvio.server import routes as app_module


def build_user() -> User:
    return User(workos_user_id=f"user_{uuid4().hex}", email="owner@example.com")


def build_run(user_id) -> RunRecord:
    return RunRecord(
        task="Open example.com",
        max_runtime_seconds=300,
        user_id=user_id,
    )


class RunOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_can_load_their_run(self) -> None:
        user = build_user()
        run = build_run(user.id)

        with patch.object(app_module.run_manager, "get_run", return_value=run):
            response = await app_module.get_run(run.id, user)

        self.assertEqual(response.run.id, run.id)

    async def test_another_accounts_run_is_reported_missing(self) -> None:
        user = build_user()
        run = build_run(uuid4())

        with patch.object(app_module.run_manager, "get_run", return_value=run):
            with self.assertRaises(HTTPException) as raised_exception:
                await app_module.get_run(run.id, user)

        self.assertEqual(raised_exception.exception.status_code, 404)

    async def test_run_without_an_owner_is_reported_missing(self) -> None:
        """Runs recorded before sign-in existed belong to nobody."""
        user = build_user()
        run = build_run(None)

        with patch.object(app_module.run_manager, "get_run", return_value=run):
            with self.assertRaises(HTTPException) as raised_exception:
                await app_module.get_run(run.id, user)

        self.assertEqual(raised_exception.exception.status_code, 404)

    async def test_screen_frames_are_refused_to_other_accounts(self) -> None:
        user = build_user()
        run = build_run(uuid4())

        with patch.object(app_module.run_manager, "get_run", return_value=run):
            with patch.object(
                app_module.run_manager,
                "get_latest_frame",
                return_value=b"jpeg-frame",
            ) as get_latest_frame:
                with self.assertRaises(HTTPException) as raised_exception:
                    await app_module.get_run_screen(run.id, user)

        self.assertEqual(raised_exception.exception.status_code, 404)
        get_latest_frame.assert_not_called()

    async def test_cancel_is_refused_to_other_accounts(self) -> None:
        user = build_user()
        run = build_run(uuid4())

        with patch.object(app_module.run_manager, "get_run", return_value=run):
            with patch.object(app_module.run_manager, "cancel_run") as cancel_run:
                with self.assertRaises(HTTPException) as raised_exception:
                    await app_module.cancel_run(run.id, user)

        self.assertEqual(raised_exception.exception.status_code, 404)
        cancel_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
