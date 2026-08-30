import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from tabvio.runs.service import RunCapacityReachedError
from tabvio.server import routes as app_module
from tabvio.server.schemas import CreateRunRequest


class CapacityEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_error_returns_too_many_requests(self) -> None:
        with patch.object(
            app_module.run_manager,
            "create_run",
            AsyncMock(
                side_effect=RunCapacityReachedError(
                    "The demo is currently at capacity. Try again shortly."
                )
            ),
        ):
            with self.assertRaises(HTTPException) as raised_exception:
                await app_module.create_run(CreateRunRequest(task="Open example.com"))

        self.assertEqual(raised_exception.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
