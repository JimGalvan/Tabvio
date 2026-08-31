import asyncio
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID

from langgraph.types import Command

from tabvio.agent.runtime import build_agent_runtime
from tabvio.clock import utc_now
from tabvio.runs import constants
from tabvio.runs.exceptions import (
    RunCapacityReachedError,
    RunNotFoundError,
    RunNotReadyForFollowUpError,
    RunNotWaitingForInputError,
)
from tabvio.runs.models import RunContext, RunEvent, RunRecord, RunStatus
from tabvio.runs.repository import RunRepository


class RunManager:
    def __init__(
            self,
            repository: RunRepository,
            headless: bool = True,
            max_concurrent_runs: int = constants.DEFAULT_MAX_CONCURRENT_RUNS,
            follow_up_window_seconds: float = constants.DEFAULT_FOLLOW_UP_WINDOW_SECONDS,
    ):
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be at least 1")
        if follow_up_window_seconds <= 0:
            raise ValueError("follow_up_window_seconds must be greater than 0")

        self._repository = repository
        self._headless = headless
        self._contexts: dict[UUID, RunContext] = {}
        self._manager_lock = asyncio.Lock()
        self._max_concurrent_runs = max_concurrent_runs
        self._follow_up_window_seconds = follow_up_window_seconds
        self._active_run_ids: set[UUID] = set()
        self._completed_frames: OrderedDict[UUID, bytes] = OrderedDict()
        self._resuming_run_ids: set[UUID] = set()

    async def create_run(
            self,
            task: str,
            max_runtime_seconds: int,
            user_id: UUID | None = None,
    ) -> RunRecord:
        async with self._manager_lock:
            if len(self._active_run_ids) >= self._max_concurrent_runs:
                raise RunCapacityReachedError("The demo is currently at capacity. Try again shortly.")

            run = RunRecord(
                task=task.strip(),
                max_runtime_seconds=max_runtime_seconds,
                user_id=user_id,
            )
            runtime = build_agent_runtime(run.thread_id, headless=self._headless)
            context = RunContext(run=run, runtime=runtime)
            self._contexts[run.id] = context
            self._active_run_ids.add(run.id)

            try:
                self._repository.save_run(run)
                await self._publish(context, "run.created", {"task": run.task, "status": run.status.value})
                context.capture_task = asyncio.create_task(self._capture_frames(context), name=f"capture-{run.id}")
                context.execution_task = asyncio.create_task(
                    self._execute(context, {"messages": [{"role": "user", "content": run.task}]}), name=f"run-{run.id}"
                )
            except Exception:
                self._contexts.pop(run.id, None)
                self._active_run_ids.discard(run.id)
                await runtime.browser.close()
                raise

            return run

    def list_runs(
            self,
            user_id: UUID,
            limit: int = constants.MAX_LISTED_RUNS,
    ) -> list[RunRecord]:
        """Recent runs for one account, newest first.

        A run that is still in flight is read from its live context so the
        history shows its current status rather than the last one written.
        """
        stored_runs = self._repository.list_runs_for_user(user_id, limit)
        return [
            self._contexts[run.id].run if run.id in self._contexts else run
            for run in stored_runs
        ]

    def _resolve_owned(
            self,
            run_id: UUID,
            user_id: UUID,
    ) -> tuple[RunContext | None, RunRecord]:
        """Find a run the account owns, live context first, stored record after.

        Someone else's run raises RunNotFoundError rather than a distinct
        error, so a run identifier cannot be used to confirm a run exists.
        Runs recorded before accounts existed have no owner and match nobody.
        """
        context = self._contexts.get(run_id)
        run = context.run if context is not None else self._repository.get_run(run_id)
        if run is None or run.user_id != user_id:
            raise RunNotFoundError(f"Run {run_id} was not found")

        return context, run

    def get_run(self, run_id: UUID, user_id: UUID) -> RunRecord:
        _, run = self._resolve_owned(run_id, user_id)
        return run

    async def submit_input(self, run_id: UUID, user_id: UUID, answer: str) -> RunRecord:
        context, _ = self._resolve_owned(run_id, user_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")

        if context.run.status != RunStatus.WAITING_FOR_INPUT:
            raise RunNotWaitingForInputError("The run is not waiting for user input")

        self._resuming_run_ids.add(run_id)
        try:
            await self._publish(context, "input.received", {"answer": answer.strip()})
            context.execution_task = asyncio.create_task(
                self._execute(context, Command(resume=answer.strip())), name=f"resume-{run_id}"
            )
            return context.run
        except Exception:
            self._resuming_run_ids.discard(run_id)
            raise

    async def submit_follow_up(self, run_id: UUID, user_id: UUID, task: str) -> RunRecord:
        expiry_task = None
        async with self._manager_lock:
            context, _ = self._resolve_owned(run_id, user_id)
            if context is None:
                raise RunNotFoundError(f"Run {run_id} was not found")

            if context.run.status != RunStatus.READY_FOR_FOLLOW_UP:
                raise RunNotReadyForFollowUpError("The run is not ready for a follow-up")

            expiry_task = context.follow_up_expiry_task
            context.follow_up_expiry_task = None
            if expiry_task is not None:
                expiry_task.cancel()

            follow_up_task = task.strip()
            context.run.follow_up_expires_at = None
            context.run.final_output = None
            context.run.error = None
            context.assistant_output_parts = []
            await self._publish(context, "follow_up.started", {"task": follow_up_task})
            await self._set_status(context, RunStatus.RUNNING)
            context.capture_task = asyncio.create_task(self._capture_frames(context), name=f"capture-{run_id}")
            context.execution_task = asyncio.create_task(
                self._execute(context, {"messages": [{"role": "user", "content": follow_up_task}]})
                , name=f"follow-up-{run_id}")

        await self._await_cancelled_task(expiry_task)
        return context.run

    async def end_session(self, run_id: UUID, user_id: UUID) -> RunRecord:
        expiry_task = None
        async with self._manager_lock:
            context, run = self._resolve_owned(run_id, user_id)
            if context is None:
                if run.status.is_terminal:
                    return run
                raise RunNotFoundError(f"Run {run_id} was not found")

            if context.run.status != RunStatus.READY_FOR_FOLLOW_UP:
                raise RunNotReadyForFollowUpError("The run is not ready to end its follow-up window")

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

    async def cancel_run(self, run_id: UUID, user_id: UUID) -> RunRecord:
        context, run = self._resolve_owned(run_id, user_id)
        if context is None:
            if not run.status.is_terminal:
                raise RunNotFoundError(f"Run {run_id} was not found")
            return run

        if context.run.status.is_terminal:
            return context.run

        if context.execution_task is not None:
            context.execution_task.cancel()
            try:
                await context.execution_task
            except asyncio.CancelledError:
                pass

        if not context.run.status.is_terminal:
            await self._publish(context, "run.cancelled", {})
            await self._set_status(context, RunStatus.CANCELLED)
            await self._finish_context(context)

        return context.run

    def get_latest_frame(self, run_id: UUID, user_id: UUID) -> bytes | None:
        context, _ = self._resolve_owned(run_id, user_id)
        if context is not None:
            return context.latest_frame

        completed_frame = self._completed_frames.get(run_id)
        if completed_frame is not None:
            self._completed_frames.move_to_end(run_id)
            return completed_frame

        return None

    def stream_events(
            self,
            run_id: UUID,
            user_id: UUID,
            after_sequence: int = 0,
    ) -> AsyncIterator[RunEvent]:
        """Events for a run the account owns, live if it is still running.

        Ownership is settled here rather than inside the generator so that an
        unauthorised caller is refused before the streaming response starts,
        while its headers can still be changed.
        """
        context, _ = self._resolve_owned(run_id, user_id)
        return self._stream_events(run_id, context, after_sequence)

    async def _stream_events(
            self,
            run_id: UUID,
            context: RunContext | None,
            after_sequence: int,
    ) -> AsyncIterator[RunEvent]:
        if context is None:
            for event in self._repository.list_events(run_id, after_sequence):
                yield event
            return

        next_sequence = after_sequence + 1
        while True:
            async with context.event_condition:
                available_events = [
                    event for event in context.events if event.sequence >= next_sequence
                ]
                while not available_events and not context.run.status.is_terminal:
                    await context.event_condition.wait()
                    available_events = [
                        event
                        for event in context.events
                        if event.sequence >= next_sequence
                    ]

                is_terminal = context.run.status.is_terminal

            for event in available_events:
                next_sequence = event.sequence + 1
                yield event

            if is_terminal:
                return

    def stream_frames(self, run_id: UUID, user_id: UUID) -> AsyncIterator[bytes]:
        context, _ = self._resolve_owned(run_id, user_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")

        return self._stream_frames(context)

    async def _stream_frames(self, context: RunContext) -> AsyncIterator[bytes]:
        delivered_frame_sequence = 0
        while True:
            async with context.frame_condition:
                has_new_frame = context.latest_frame is not None and context.frame_sequence > delivered_frame_sequence
                while not has_new_frame and not context.run.status.is_terminal:
                    await context.frame_condition.wait()
                    has_new_frame = context.latest_frame is not None and context.frame_sequence > delivered_frame_sequence

                is_terminal = context.run.status.is_terminal
                frame = context.latest_frame if has_new_frame else None
                if has_new_frame:
                    delivered_frame_sequence = context.frame_sequence

            if frame is not None:
                yield frame

            if is_terminal:
                return

    async def shutdown(self) -> None:
        expiry_tasks = []
        for context in self._contexts.values():
            if context.follow_up_expiry_task is not None:
                context.follow_up_expiry_task.cancel()
                expiry_tasks.append(context.follow_up_expiry_task)
                context.follow_up_expiry_task = None

        if expiry_tasks:
            await asyncio.gather(*expiry_tasks, return_exceptions=True)

        contexts = list(self._contexts.values())
        for context in contexts:
            if context.execution_task is not None:
                context.execution_task.cancel()
            if context.capture_task is not None:
                context.capture_task.cancel()
            await context.runtime.browser.close()
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
            context.run.follow_up_expires_at = utc_now() + timedelta(seconds=self._follow_up_window_seconds)
            await self._publish(context, "run.completed",
                                {"output": final_output,
                                 "follow_up_expires_at": context.run.follow_up_expires_at.isoformat()})
            await self._set_status(context, RunStatus.READY_FOR_FOLLOW_UP)
            await self._pause_frame_capture(context)
            context.execution_task = None
            context.follow_up_expiry_task = asyncio.create_task(self._expire_follow_up_window(context),
                                                                name=f"follow-up-expiry-{context.run.id}")
        except TimeoutError:
            context.run.follow_up_expires_at = None
            await self._publish(context, "run.failed", {"error": "The run exceeded its execution time limit"})
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
            await self._publish(context, "run.failed", {"error": str(exception)})
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
                frame = await asyncio.wait_for(context.runtime.browser.capture_frame(),
                                               timeout=constants.FRAME_CAPTURE_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exception:
                logger.warning("Browser frame capture failed for run %s: %s", context.run.id, exception)
                if not capture_failure_active:
                    capture_failure_active = True
                    await self._publish(context, "browser.capture.failed",
                                        {"message": "Live view paused; retrying automatically"})

                await asyncio.sleep(constants.FRAME_RETRY_INTERVAL_SECONDS)
                continue

            if frame is not None:
                async with context.frame_condition:
                    context.latest_frame = frame
                    context.frame_sequence += 1
                    context.frame_condition.notify_all()

                if capture_failure_active:
                    capture_failure_active = False
                    await self._publish(context, "browser.capture.recovered",
                                        {"message": "Live view resumed"})

            await asyncio.sleep(constants.FRAME_INTERVAL_SECONDS)

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
            while len(self._completed_frames) > constants.MAX_COMPLETED_FRAME_COUNT:
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
        event = RunEvent(run_id=context.run.id, sequence=context.next_event_sequence, event_type=event_type,
                         payload=self._json_safe(payload))
        context.next_event_sequence += 1
        context.events.append(event)

        if persist:
            self._repository.save_event(event)

        async with context.event_condition:
            context.event_condition.notify_all()

        if event_type == "agent.message.delta":
            self._trim_live_message_events(context)

        return event

    async def _set_status(
            self,
            context: RunContext,
            status: RunStatus,
    ) -> None:
        if context.run.status == status:
            return

        context.run.status = status
        context.run.updated_at = utc_now()
        self._repository.save_run(context.run)
        status_payload = {"status": status.value}
        if context.run.follow_up_expires_at is not None:
            status_payload["follow_up_expires_at"] = context.run.follow_up_expires_at.isoformat()
        await self._publish(context, "run.status", status_payload)

    async def _handle_stream_part(
            self,
            context: RunContext,
            stream_part: dict[str, Any],
    ) -> None:
        stream_type = stream_part.get("type")
        data = stream_part.get("data")

        if stream_type == "custom" and isinstance(data, dict):
            event_type = data.get("event_type")
            if event_type == "input.required" and context.run.id in self._resuming_run_ids:
                self._resuming_run_ids.discard(context.run.id)
                return

            payload = data.get("payload", {})
            if isinstance(event_type, str) and isinstance(payload, dict):
                await self._publish(context, event_type, payload)
                if event_type == "input.required":
                    await self._set_status(context, RunStatus.WAITING_FOR_INPUT)
            return

        if stream_type == "messages":
            message_text = self._extract_stream_message(data)
            if message_text:
                context.assistant_output_parts.append(message_text)
                await self._publish(context, "agent.message.delta",
                                    {"text": message_text}, persist=False)
                self._trim_assistant_output(context)
            return

        if stream_type == "updates" and self._contains_interrupt(data):
            if context.run.status != RunStatus.WAITING_FOR_INPUT:
                await self._set_status(context, RunStatus.WAITING_FOR_INPUT)

    async def _get_final_output(self, context: RunContext) -> str:
        try:
            state = await context.runtime.agent.aget_state(context.runtime.config)
            messages = state.values.get("messages", [])
            if messages:
                message_text = self._extract_message_content(messages[-1].content)
                if message_text:
                    return message_text
        except Exception:
            pass

        return "".join(context.assistant_output_parts).strip()

    def _trim_live_message_events(self, context: RunContext) -> None:
        message_event_count = 0
        for event in context.events:
            if event.event_type == "agent.message.delta":
                message_event_count += 1

        events_to_remove = message_event_count - constants.MAX_LIVE_MESSAGE_EVENT_COUNT
        if events_to_remove <= 0:
            return

        retained_events = []
        for event in context.events:
            if event.event_type == "agent.message.delta" and events_to_remove > 0:
                events_to_remove -= 1
                continue
            retained_events.append(event)

        context.events = retained_events

    def _trim_assistant_output(self, context: RunContext) -> None:
        output_character_count = 0
        for output_part in context.assistant_output_parts:
            output_character_count += len(output_part)

        if output_character_count <= constants.MAX_ASSISTANT_OUTPUT_CHARACTERS:
            return

        combined_output = "".join(context.assistant_output_parts)
        context.assistant_output_parts = [combined_output[-constants.MAX_ASSISTANT_OUTPUT_CHARACTERS:]]

    def _extract_stream_message(self, data: Any) -> str:
        if not isinstance(data, (tuple, list)) or not data:
            return ""

        message = data[0]
        message_type = getattr(message, "type", None)
        if message_type not in {"ai", "assistant", "AIMessageChunk"}:
            return ""

        content = getattr(message, "content", "")
        return self._extract_message_content(content)

    def _extract_message_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content

        if not isinstance(content, list):
            return ""

        text_parts = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    text_parts.append(text)

        return "".join(text_parts)

    def _contains_interrupt(self, value: Any) -> bool:
        if isinstance(value, dict):
            if "__interrupt__" in value:
                return True

            for nested_value in value.values():
                if self._contains_interrupt(nested_value):
                    return True

        if isinstance(value, (list, tuple)):
            for nested_value in value:
                if self._contains_interrupt(nested_value):
                    return True

        return False

    def _json_safe(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value

        if isinstance(value, dict):
            return {
                str(key): self._json_safe(nested_value)
                for key, nested_value in value.items()
            }

        if isinstance(value, (list, tuple)):
            return [self._json_safe(nested_value) for nested_value in value]

        return str(value)


logger = logging.getLogger(__name__)

__all__ = ["RunManager"]
