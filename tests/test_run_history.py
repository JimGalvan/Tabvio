import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tabvio.runs.repository import RunRepository
from tabvio.server import routes as app_module
from tests.support import build_run, signed_in_client


class RunHistoryRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = RunRepository(Path(self.directory.name) / "tabvio.db")
        self.repository.initialize()

    def test_only_the_owners_runs_are_listed(self) -> None:
        owner_id = uuid4()
        self.repository.save_run(build_run(owner_id, "Mine"))
        self.repository.save_run(build_run(uuid4(), "Theirs"))
        self.repository.save_run(build_run(None, "Nobody's"))

        listed = self.repository.list_runs_for_user(owner_id, limit=50)

        self.assertEqual([run.task for run in listed], ["Mine"])

    def test_runs_are_listed_newest_first(self) -> None:
        owner_id = uuid4()
        older = build_run(owner_id, "Older")
        newer = build_run(owner_id, "Newer")
        newer.created_at = older.created_at.replace(year=older.created_at.year + 1)
        self.repository.save_run(older)
        self.repository.save_run(newer)

        listed = self.repository.list_runs_for_user(owner_id, limit=50)

        self.assertEqual([run.task for run in listed], ["Newer", "Older"])

    def test_runs_created_in_the_same_instant_have_a_stable_order(self) -> None:
        """Two runs can share a timestamp, and the list must not shuffle between reads."""
        owner_id = uuid4()
        first = build_run(owner_id, "First")
        second = build_run(owner_id, "Second")
        second.created_at = first.created_at
        self.repository.save_run(first)
        self.repository.save_run(second)

        orders = {
            tuple(run.id for run in self.repository.list_runs_for_user(owner_id, limit=50))
            for _ in range(5)
        }

        self.assertEqual(len(orders), 1)

    def test_the_limit_is_applied(self) -> None:
        owner_id = uuid4()
        for index in range(5):
            self.repository.save_run(build_run(owner_id, f"Task {index}"))

        self.assertEqual(len(self.repository.list_runs_for_user(owner_id, limit=3)), 3)


class RunHistoryEndpointTests(unittest.TestCase):
    def test_history_returns_the_callers_runs(self) -> None:
        with signed_in_client() as (client, user):
            runs = [build_run(user.id, "Mine"), build_run(user.id, "Also mine")]
            with patch.object(
                app_module.repository, "list_runs_for_user", return_value=runs
            ):
                response = client.get("/api/runs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [run["task"] for run in response.json()["runs"]], ["Mine", "Also mine"]
        )

    def test_history_asks_only_for_the_callers_runs(self) -> None:
        with signed_in_client() as (client, user):
            with patch.object(
                app_module.repository, "list_runs_for_user", return_value=[]
            ) as list_runs_for_user:
                client.get("/api/runs")

        list_runs_for_user.assert_called_once()
        self.assertEqual(list_runs_for_user.call_args.args[0], user.id)

    def test_history_hides_internal_fields(self) -> None:
        """The thread and owner identifiers must not reach the browser."""
        with signed_in_client() as (client, user):
            with patch.object(
                app_module.repository,
                "list_runs_for_user",
                return_value=[build_run(user.id, "Mine")],
            ):
                response = client.get("/api/runs")

        listed = response.json()["runs"][0]
        self.assertNotIn("thread_id", listed)
        self.assertNotIn("user_id", listed)


if __name__ == "__main__":
    unittest.main()
