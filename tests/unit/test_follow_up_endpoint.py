import unittest
from unittest.mock import AsyncMock, patch

from tabvio.runs.exceptions import RunNotReadyForFollowUpError
from tabvio.server import routes as app_module
from tests.support import build_run, signed_in_client


class FollowUpEndpointTests(unittest.TestCase):
    def test_follow_up_conflict_returns_http_409(self) -> None:
        with signed_in_client() as (client, user):
            run = build_run(user.id)
            with patch.object(app_module.repository, "get_run", return_value=run):
                with patch.object(
                    app_module.run_manager,
                    "submit_follow_up",
                    AsyncMock(
                        side_effect=RunNotReadyForFollowUpError(
                            "The run is not ready for a follow-up"
                        )
                    ),
                ):
                    response = client.post(
                        f"/api/runs/{run.id}/follow-ups", json={"task": "Add an item"}
                    )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "The run is not ready for a follow-up")


if __name__ == "__main__":
    unittest.main()
