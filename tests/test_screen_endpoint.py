import unittest
from unittest.mock import patch
from uuid import uuid4

from tabvio.runs.models import RunRecord
from tabvio.server import routes as app_module


class ScreenEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_screen_endpoint_returns_latest_jpeg(self) -> None:
        run_id = uuid4()
        with patch.object(
            app_module.run_manager,
            "get_latest_frame",
            return_value=b"jpeg-frame",
        ):
            response = await app_module.get_run_screen(run_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "image/jpeg")
        self.assertEqual(response.body, b"jpeg-frame")

    async def test_screen_endpoint_returns_no_content_before_first_frame(self) -> None:
        run_id = uuid4()
        with patch.object(
            app_module.run_manager,
            "get_latest_frame",
            return_value=None,
        ):
            response = await app_module.get_run_screen(run_id)

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
