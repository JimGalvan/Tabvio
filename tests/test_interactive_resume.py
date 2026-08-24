import asyncio
import unittest
from types import SimpleNamespace
from uuid import uuid4

from run_manager import RunManager


class InteractiveResumeTests(unittest.TestCase):
    def test_replayed_input_event_is_suppressed_once(self) -> None:
        run_id = uuid4()
        manager = RunManager.__new__(RunManager)
        manager._resuming_run_ids = {run_id}
        context = SimpleNamespace(run=SimpleNamespace(id=run_id))
        stream_part = {
            "type": "custom",
            "data": {
                "event_type": "input.required",
                "payload": {"question": "What word should I use?"},
            },
        }

        asyncio.run(manager._handle_stream_part(context, stream_part))

        self.assertNotIn(run_id, manager._resuming_run_ids)


if __name__ == "__main__":
    unittest.main()
