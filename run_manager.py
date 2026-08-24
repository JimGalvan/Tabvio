import asyncio
import logging
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

logger = logging.getLogger(__name__)


class RunManager(CoreRunManager):
    FRAME_CAPTURE_TIMEOUT_SECONDS = 5.0
    FRAME_RETRY_INTERVAL_SECONDS = 1.0

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

    def get_latest_frame(self, run_id: UUID) -> bytes | None:
        context = self._contexts.get(run_id)
        if context is not None:
            return context.latest_frame

        if self._repository.get_run(run_id) is None:
            raise RunNotFoundError(f"Run {run_id} was not found")

        return None

    async def _capture_frames(self, context: RunContext) -> None:
        capture_failure_active = False

        while not context.run.status.is_terminal:
            try:
                frame = await asyncio.wait_for(
                    context.runtime.browser.capture_frame(),
                    timeout=self.FRAME_CAPTURE_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exception:
                logger.warning(
                    "Browser frame capture failed for run %s: %s",
                    context.run.id,
                    exception,
                )
                if not capture_failure_active:
                    capture_failure_active = True
                    await self._publish(
                        context,
                        "browser.capture.failed",
                        {"message": "Live view paused; retrying automatically"},
                    )

                await asyncio.sleep(self.FRAME_RETRY_INTERVAL_SECONDS)
                continue

            if frame is not None:
                async with context.frame_condition:
                    context.latest_frame = frame
                    context.frame_sequence += 1
                    context.frame_condition.notify_all()

                if capture_failure_active:
                    capture_failure_active = False
                    await self._publish(
                        context,
                        "browser.capture.recovered",
                        {"message": "Live view resumed"},
                    )

            await asyncio.sleep(self.FRAME_INTERVAL_SECONDS)

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
