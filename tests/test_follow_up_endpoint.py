import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from tabvio.auth.models import User
from tabvio.runs.exceptions import RunNotReadyForFollowUpError
from tabvio.runs.models import RunRecord
from tabvio.server import routes as app_module
from tabvio.server.schemas import FollowUpRequest


class FollowUpEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_follow_up_conflict_returns_http_409(self) -> None:
        user = User(workos_user_id="user_01ABC", email="owner@example.com")
        run = RunRecord(
            task="Open example.com",
            max_runtime_seconds=300,
            user_id=user.id,
        )

        with patch.object(app_module.run_manager, "get_run", return_value=run):
            with patch.object(
                app_module.run_manager,
                "submit_follow_up",
                AsyncMock(
                    side_effect=RunNotReadyForFollowUpError(
                        "The run is not ready for a follow-up"
                    )
                ),
            ):
                with self.assertRaises(HTTPException) as raised_exception:
                    await app_module.submit_follow_up(
                        run_id=run.id,
                        request=FollowUpRequest(task="Add an item"),
                        user=user,
                    )

        self.assertEqual(raised_exception.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
