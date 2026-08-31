import unittest
from unittest.mock import AsyncMock, patch

from tabvio.runs.exceptions import RunCapacityReachedError
from tabvio.server import routes as app_module
from tests.support import signed_in_client


class CapacityEndpointTests(unittest.TestCase):
    def test_capacity_error_returns_too_many_requests(self) -> None:
        with signed_in_client() as (client, _):
            with patch.object(
                app_module.run_manager,
                "create_run",
                AsyncMock(
                    side_effect=RunCapacityReachedError(
                        "The demo is currently at capacity. Try again shortly."
                    )
                ),
            ):
                response = client.post("/api/runs", json={"task": "Open example.com"})

        self.assertEqual(response.status_code, 429)
        self.assertIn("capacity", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
