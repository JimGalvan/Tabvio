from typing import Any
from uuid import UUID

from run_manager_core import (
    RunAlreadyActiveError,
    RunContext,
    RunNotFoundError,
    RunNotWaitingForInputError,
)
from run_manager_core import (
    RunManager as CoreRunManager,
)
from run_models import RunRecord
from run_repository import RunRepository


class RunManager(CoreRunManager):
    def __init__(self, repository: RunRepository, headless: bool = True):
        super().__init__(repository=repository, headless=headless)
        self._resuming_run_ids: set[UUID] = set()

    async def submit_input(self, run_id: UUID, answer: str) -> RunRecord:
        self._resuming_run_ids.add(run_id)
        try:
            return await super().submit_input(run_id, answer)
        except Exception:
            self._resuming_run_ids.discard(run_id)
            raise

    async def _handle_stream_part(
        self,
        context: RunContext,
        stream_part: dict[str, Any],
    ) -> None:
        stream_type = stream_part.get("type")
        data = stream_part.get("data")
        if stream_type == "custom" and isinstance(data, dict):
            event_type = data.get("event_type")
            if (
                event_type == "input.required"
                and context.run.id in self._resuming_run_ids
            ):
                self._resuming_run_ids.discard(context.run.id)
                return

        await super()._handle_stream_part(context, stream_part)

    def _extract_stream_message(self, data: Any) -> str:
        if not isinstance(data, (tuple, list)) or not data:
            return ""

        message = data[0]
        message_type = getattr(message, "type", None)
        if message_type not in {"ai", "assistant", "AIMessageChunk"}:
            return ""

        content = getattr(message, "content", "")
        return self._extract_message_content(content)


__all__ = [
    "RunAlreadyActiveError",
    "RunManager",
    "RunNotFoundError",
    "RunNotWaitingForInputError",
]
