import unittest
from unittest.mock import patch

from tabvio.auth.models import User
from tabvio.runs.models import RunRecord
from tabvio.server import routes as app_module


def build_user() -> User:
    return User(workos_user_id="user_01ABC", email="owner@example.com")


class ScreenEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_screen_endpoint_returns_latest_jpeg(self) -> None:
        user = build_user()
        run = RunRecord(
            task="Open example.com",
            max_runtime_seconds=300,
            user_id=user.id,
        )

        with patch.object(app_module.run_manager, "get_run", return_value=run):
            with patch.object(
                app_module.run_manager,
                "get_latest_frame",
                return_value=b"jpeg-frame",
            ):
                response = await app_module.get_run_screen(run.id, user)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "image/jpeg")
        self.assertEqual(response.body, b"jpeg-frame")

    async def test_screen_endpoint_returns_no_content_before_first_frame(self) -> None:
        user = build_user()
        run = RunRecord(
            task="Open example.com",
            max_runtime_seconds=300,
            user_id=user.id,
        )

        with patch.object(app_module.run_manager, "get_run", return_value=run):
            with patch.object(
                app_module.run_manager,
                "get_latest_frame",
                return_value=None,
            ):
                response = await app_module.get_run_screen(run.id, user)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.body, b"")

    async def test_run_response_uses_snapshot_endpoint(self) -> None:
        run = RunRecord(task="Open example.com", max_runtime_seconds=300)

        response = app_module._build_run_response(run)

        self.assertEqual(
            response.screen_url,
            f"/api/runs/{run.id}/screen.jpg",
        )


if __name__ == "__main__":
    unittest.main()
