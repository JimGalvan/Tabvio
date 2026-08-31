import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tabvio.auth.models import User
from tabvio.runs.models import RunRecord, RunStatus
from tabvio.runs.repository import RunRepository
from tabvio.server import routes as app_module


def build_run(user_id, task: str, status: RunStatus = RunStatus.SUCCEEDED) -> RunRecord:
    return RunRecord(
        task=task,
        max_runtime_seconds=300,
        user_id=user_id,
        status=status,
    )


class RunHistoryRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = RunRepository(Path(self.directory.name) / "tabvio.db")
        self.repository.initialize()

    def test_only_the_owners_runs_are_listed(self) -> None:
        owner_id = uuid4()
        other_id = uuid4()
        self.repository.save_run(build_run(owner_id, "Mine"))
        self.repository.save_run(build_run(other_id, "Theirs"))
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

    def test_the_limit_is_applied(self) -> None:
        owner_id = uuid4()
        for index in range(5):
            self.repository.save_run(build_run(owner_id, f"Task {index}"))

        self.assertEqual(len(self.repository.list_runs_for_user(owner_id, limit=3)), 3)


class RunHistoryEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_returns_the_callers_runs(self) -> None:
        user = User(workos_user_id="user_01ABC", email="owner@example.com")
        runs = [build_run(user.id, "Mine"), build_run(user.id, "Also mine")]

        with patch.object(app_module.run_manager, "list_runs", return_value=runs):
            response = await app_module.list_runs(user)

        self.assertEqual([run.task for run in response.runs], ["Mine", "Also mine"])

    async def test_history_hides_internal_fields(self) -> None:
        """Thread and owner identifiers stay server-side."""
        user = User(workos_user_id="user_01ABC", email="owner@example.com")

        with patch.object(
            app_module.run_manager,
            "list_runs",
            return_value=[build_run(user.id, "Mine")],
        ):
            response = await app_module.list_runs(user)

        serialized = response.runs[0].model_dump()
        self.assertNotIn("thread_id", serialized)
        self.assertNotIn("user_id", serialized)


if __name__ == "__main__":
    unittest.main()
