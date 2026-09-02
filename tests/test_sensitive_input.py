import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from tabvio.agent.sensitive_input import SensitiveInputChannel
from tabvio.runs.models import RunContext, RunRecord, RunStatus
from tabvio.runs.repository import RunRepository
from tabvio.runs.service import RunManager


class SensitiveBrowser:
    def __init__(self):
        self.fills = []

    async def fill_sensitive(self, element_index, value):
        self.fills.append((element_index, value))


class SensitiveInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self._repository = RunRepository(
            Path(self._temporary_directory.name) / "tabvio.db"
        )
        self._repository.initialize()
        self._manager = RunManager(self._repository)
        self._manager._execute = AsyncMock()

    async def asyncTearDown(self) -> None:
        self._temporary_directory.cleanup()

    async def test_code_is_filled_but_never_persisted(self) -> None:
        owner_id = uuid4()
        run = RunRecord(
            task="Sign in",
            max_runtime_seconds=300,
            user_id=owner_id,
            status=RunStatus.WAITING_FOR_INPUT,
        )
        browser = SensitiveBrowser()
        channel = SensitiveInputChannel()
        pending = channel.begin(8, "Enter the code")
        runtime = SimpleNamespace(browser=browser, sensitive_inputs=channel)
        context = RunContext(run=run, runtime=runtime)
        self._repository.save_run(run)
        self._manager._contexts[run.id] = context
        self._manager._active_run_ids.add(run.id)

        result = await self._manager.submit_sensitive_input(
            run.id, owner_id, pending.id, "123456"
        )
        await asyncio.sleep(0)

        self.assertIs(result, run)
        self.assertEqual(browser.fills, [(8, "123456")])
        events = self._repository.list_events(run.id)
        self.assertEqual(events[-1].event_type, "sensitive_input.received")
        self.assertNotIn("123456", events[-1].model_dump_json())
        self._manager._execute.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
