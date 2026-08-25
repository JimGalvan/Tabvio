import asyncio
import logging
from collections import OrderedDict
from datetime import timedelta
from typing import Any
from uuid import UUID

from agent_runtime import build_agent_runtime
from run_manager_core import (
    RunAlreadyActiveError,
    RunContext,
    RunNotFoundError,
    RunNotReadyForFollowUpError,
    RunNotWaitingForInputError,
)
from run_manager_core import (
    RunManager as CoreRunManager,
)
from run_models import RunEvent, RunRecord, RunStatus, utc_now
from run_repository import RunRepository

logger = logging.getLogger(__name__)


class RunCapacityReachedError(RuntimeError):
    pass


class RunManager(CoreRunManager):
    DEFAULT_MAX_CONCURRENT_RUNS = 3
    MAX_COMPLETED_FRAME_COUNT = 6
    MAX_LIVE_MESSAGE_EVENT_COUNT = 600
    MAX_ASSISTANT_OUTPUT_CHARACTERS = 100_000
    FRAME_CAPTURE_TIMEOUT_SECONDS = 5.0
    FRAME_RETRY_INTERVAL_SECONDS = 1.0
    DEFAULT_FOLLOW_UP_WINDOW_SECONDS = 300

    def __init__(
        self,
        repository: RunRepository,
        headless: bool = True,
        max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS,
        follow_up_window_seconds: float = DEFAULT_FOLLOW_UP_WINDOW_SECONDS,
    ):
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be at least 1")
        if follow_up_window_seconds <= 0:
            raise ValueError("follow_up_window_seconds must be greater than 0")

        super().__init__(repository=repository, headless=headless)
        self._max_concurrent_runs = max_concurrent_runs
        self._follow_up_window_seconds = follow_up_window_seconds
        self._active_run_ids: set[UUID] = set()
        self._completed_frames: OrderedDict[UUID, bytes] = OrderedDict()
        self._resuming_run_ids: set[UUID] = set()

    async def create_run(
        self,
        task: str,
        max_runtime_seconds: int,
    ) -> RunRecord:
        async with self._manager_lock:
            if len(self._active_run_ids) >= self._max_concurrent_runs:
                raise RunCapacityReachedError(
                    "The demo is currently at capacity. Try again shortly."
                )

            run = RunRecord(
                task=task.strip(),
                max_runtime_seconds=max_runtime_seconds,
            )
            runtime = build_agent_runtime(run.thread_id, headless=self._headless)
            context = RunContext(run=run, runtime=runtime)
            self._contexts[run.id] = context
            self._active_run_ids.add(run.id)

            try:
                self._repository.save_run(run)
                await self._publish(
                    context,
                    "run.created",
                    {"task": run.task, "status": run.status.value},
                )
                context.capture_task = asyncio.create_task(
                    self._capture_frames(context),
                    name=f"capture-{run.id}",
                )
                context.execution_task = asyncio.create_task(
                    self._execute(
                        context,
                        {"messages": [{"role": "user", "content": run.task}]},
                    ),
                    name=f"run-{run.id}",
                )
            except Exception:
                self._contexts.pop(run.id, None)
                self._active_run_ids.discard(run.id)
                await runtime.browser.close()
                raise

            return run

    async def submit_input(self, run_id: UUID, answer: str) -> RunRecord:
        self._resuming_run_ids.add(run_id)
        try:
            return await super().submit_input(run_id, answer)
        except Exception:
            self._resuming_run_ids.discard(run_id)
            raise

    async def submit_follow_up(self, run_id: UUID, task: str) -> RunRecord:
        expiry_task = None
        async with self._manager_lock:
            context = self._contexts.get(run_id)
            if context is None:
                raise RunNotFoundError(f"Run {run_id} was not found")

            if context.run.status != RunStatus.READY_FOR_FOLLOW_UP:
                raise RunNotReadyForFollowUpError(
                    "The run is not ready for a follow-up"
                )

            expiry_task = context.follow_up_expiry_task
            context.follow_up_expiry_task = None
            if expiry_task is not None:
                expiry_task.cancel()

            follow_up_task = task.strip()
            context.run.follow_up_expires_at = None
            context.run.final_output = None
            context.run.error = None
            context.assistant_output_parts = []
            await self._publish(
                context,
                "follow_up.started",
                {"task": follow_up_task},
            )
            await self._set_status(context, RunStatus.RUNNING)
            context.capture_task = asyncio.create_task(
                self._capture_frames(context),
                name=f"capture-{run_id}",
            )
            context.execution_task = asyncio.create_task(
                self._execute(
                    context,
                    {
                        "messages": [
                            {"role": "user", "content": follow_up_task}
                        ]
                    },
                ),
                name=f"follow-up-{run_id}",
            )

        await self._await_cancelled_task(expiry_task)
        return context.run

    async def end_session(self, run_id: UUID) -> RunRecord:
        expiry_task = None
        async with self._manager_lock:
            context = self._contexts.get(run_id)
            if context is None:
                run = self._repository.get_run(run_id)
                if run is not None and run.status.is_terminal:
                    return run
                raise RunNotFoundError(f"Run {run_id} was not found")

            if context.run.status != RunStatus.READY_FOR_FOLLOW_UP:
                raise RunNotReadyForFollowUpError(
                    "The run is not ready to end its follow-up window"
                )

            expiry_task = context.follow_up_expiry_task
            context.follow_up_expiry_task = None
            if expiry_task is not None:
                expiry_task.cancel()

            context.run.follow_up_expires_at = None
            await self._publish(context, "follow_up.ended", {})
            await self._set_status(context, RunStatus.SUCCEEDED)

        await self._await_cancelled_task(expiry_task)
        await self._finish_context(context)
        return context.run

    async def cancel_run(self, run_id: UUID) -> RunRecord:
        context = self._contexts.get(run_id)
        if context is not None:
            return await super().cancel_run(run_id)

        run = self._repository.get_run(run_id)
        if run is None or not run.status.is_terminal:
            raise RunNotFoundError(f"Run {run_id} was not found")

        return run

    def get_latest_frame(self, run_id: UUID) -> bytes | None:
        context = self._contexts.get(run_id)
        if context is not None:
            return context.latest_frame

        completed_frame = self._completed_frames.get(run_id)
        if completed_frame is not None:
            self._completed_frames.move_to_end(run_id)
            return completed_frame

        if self._repository.get_run(run_id) is None:
            raise RunNotFoundError(f"Run {run_id} was not found")

        return None

    async def shutdown(self) -> None:
        expiry_tasks = []
        for context in self._contexts.values():
            if context.follow_up_expiry_task is not None:
                context.follow_up_expiry_task.cancel()
                expiry_tasks.append(context.follow_up_expiry_task)
                context.follow_up_expiry_task = None

        if expiry_tasks:
            await asyncio.gather(*expiry_tasks, return_exceptions=True)

        await super().shutdown()
        self._contexts.clear()
        self._active_run_ids.clear()
        self._completed_frames.clear()
        self._resuming_run_ids.clear()

    async def _execute(self, context: RunContext, agent_input: Any) -> None:
        await self._set_status(context, RunStatus.RUNNING)

        try:
            async with asyncio.timeout(context.run.max_runtime_seconds):
                async for stream_part in context.runtime.agent.astream(
                    agent_input,
                    config=context.runtime.config,
                    stream_mode=["messages", "custom", "updates"],
                    version="v2",
                ):
                    await self._handle_stream_part(context, stream_part)

            if context.run.status == RunStatus.WAITING_FOR_INPUT:
                return

            final_output = await self._get_final_output(context)
            context.run.final_output = final_output
            context.run.follow_up_expires_at = utc_now() + timedelta(
                seconds=self._follow_up_window_seconds
            )
            await self._publish(
                context,
                "run.completed",
                {
                    "output": final_output,
                    "follow_up_expires_at": (
                        context.run.follow_up_expires_at.isoformat()
                    ),
                },
            )
            await self._set_status(
                context,
                RunStatus.READY_FOR_FOLLOW_UP,
            )
            await self._pause_frame_capture(context)
            context.execution_task = None
            context.follow_up_expiry_task = asyncio.create_task(
                self._expire_follow_up_window(context),
                name=f"follow-up-expiry-{context.run.id}",
            )
        except TimeoutError:
            context.run.follow_up_expires_at = None
            await self._publish(
                context,
                "run.failed",
                {"error": "The run exceeded its execution time limit"},
            )
            await self._set_status(context, RunStatus.TIMED_OUT)
            await self._finish_context(context)
        except asyncio.CancelledError:
            context.run.follow_up_expires_at = None
            await self._publish(context, "run.cancelled", {})
            await self._set_status(context, RunStatus.CANCELLED)
            await self._finish_context(context)
            raise
        except Exception as exception:
            context.run.error = str(exception)
            context.run.follow_up_expires_at = None
            await self._publish(
                context,
                "run.failed",
                {"error": str(exception)},
            )
            await self._set_status(context, RunStatus.FAILED)
            await self._finish_context(context)

    async def _expire_follow_up_window(self, context: RunContext) -> None:
        try:
            await asyncio.sleep(self._follow_up_window_seconds)
        except asyncio.CancelledError:
            return

        async with self._manager_lock:
            current_context = self._contexts.get(context.run.id)
            if (
                current_context is not context
                or context.run.status != RunStatus.READY_FOR_FOLLOW_UP
            ):
                return

            context.follow_up_expiry_task = None
            context.run.follow_up_expires_at = None
            await self._publish(context, "follow_up.expired", {})
            await self._set_status(context, RunStatus.SUCCEEDED)

        await self._finish_context(context)

    async def _pause_frame_capture(self, context: RunContext) -> None:
        capture_task = context.capture_task
        context.capture_task = None
        if capture_task is None:
            return

        capture_task.cancel()
        await self._await_cancelled_task(capture_task)

    async def _await_cancelled_task(
        self,
        task: asyncio.Task[None] | None,
    ) -> None:
        if task is None or task is asyncio.current_task():
            return

        try:
            await task
        except asyncio.CancelledError:
            pass

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

    async def _finish_context(self, context: RunContext) -> None:
        expiry_task = context.follow_up_expiry_task
        context.follow_up_expiry_task = None
        if expiry_task is not None and expiry_task is not asyncio.current_task():
            expiry_task.cancel()
            await self._await_cancelled_task(expiry_task)

        if context.capture_task is not None:
            context.capture_task.cancel()
            try:
                await context.capture_task
            except asyncio.CancelledError:
                pass
            context.capture_task = None

        await context.runtime.browser.close()

        if context.latest_frame is not None:
            self._completed_frames[context.run.id] = context.latest_frame
            self._completed_frames.move_to_end(context.run.id)
            while len(self._completed_frames) > self.MAX_COMPLETED_FRAME_COUNT:
                self._completed_frames.popitem(last=False)

        async with self._manager_lock:
            self._active_run_ids.discard(context.run.id)
            self._contexts.pop(context.run.id, None)
            self._resuming_run_ids.discard(context.run.id)

        async with context.event_condition:
            context.event_condition.notify_all()

        async with context.frame_condition:
            context.frame_condition.notify_all()

        context.execution_task = None

    async def _publish(
        self,
        context: RunContext,
        event_type: str,
        payload: dict[str, Any],
        persist: bool = True,
    ) -> RunEvent:
        event = await super()._publish(
            context,
            event_type,
            payload,
            persist=persist,
        )
        if event_type == "agent.message.delta":
            self._trim_live_message_events(context)

        return event

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
        if stream_type == "messages":
            self._trim_assistant_output(context)

    def _trim_live_message_events(self, context: RunContext) -> None:
        message_event_count = 0
        for event in context.events:
            if event.event_type == "agent.message.delta":
                message_event_count += 1

        events_to_remove = (
            message_event_count - self.MAX_LIVE_MESSAGE_EVENT_COUNT
        )
        if events_to_remove <= 0:
            return

        retained_events = []
        for event in context.events:
            if (
                event.event_type == "agent.message.delta"
                and events_to_remove > 0
            ):
                events_to_remove -= 1
                continue
            retained_events.append(event)

        context.events = retained_events

    def _trim_assistant_output(self, context: RunContext) -> None:
        output_character_count = 0
        for output_part in context.assistant_output_parts:
            output_character_count += len(output_part)

        if output_character_count <= self.MAX_ASSISTANT_OUTPUT_CHARACTERS:
            return

        combined_output = "".join(context.assistant_output_parts)
        context.assistant_output_parts = [
            combined_output[-self.MAX_ASSISTANT_OUTPUT_CHARACTERS :]
        ]

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
    "RunCapacityReachedError",
    "RunManager",
    "RunNotFoundError",
    "RunNotReadyForFollowUpError",
    "RunNotWaitingForInputError",
]
