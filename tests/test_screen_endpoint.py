import unittest
from collections import OrderedDict
from unittest.mock import patch

from tabvio.server import routes as app_module
from tests.support import build_run, signed_in_client


class ScreenEndpointTests(unittest.TestCase):
    def test_screen_endpoint_returns_latest_jpeg(self) -> None:
        with signed_in_client() as (client, user):
            run = build_run(user.id)
            with patch.object(app_module.repository, "get_run", return_value=run):
                with patch.object(
                    app_module.run_manager,
                    "_completed_frames",
                    OrderedDict({run.id: b"jpeg-frame"}),
                ):
                    response = client.get(f"/api/runs/{run.id}/screen.jpg")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertEqual(response.content, b"jpeg-frame")

    def test_screen_endpoint_returns_no_content_before_first_frame(self) -> None:
        with signed_in_client() as (client, user):
            run = build_run(user.id)
            with patch.object(app_module.repository, "get_run", return_value=run):
                response = client.get(f"/api/runs/{run.id}/screen.jpg")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")


if __name__ == "__main__":
    unittest.main()
