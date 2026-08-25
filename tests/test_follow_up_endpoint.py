import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException

import app as app_module
from run_manager import RunNotReadyForFollowUpError
from run_models import FollowUpRequest


class FollowUpEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_follow_up_conflict_returns_http_409(self) -> None:
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
                    run_id=uuid4(),
                    request=FollowUpRequest(task="Add an item"),
                )

        self.assertEqual(raised_exception.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
