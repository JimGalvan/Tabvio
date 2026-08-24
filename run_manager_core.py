import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langgraph.types import Command

from agent_runtime import AgentRuntime, build_agent_runtime
from run_models import RunEvent, RunRecord, RunStatus, utc_now
from run_repository import RunRepository


class RunAlreadyActiveError(RuntimeError):
    pass


class RunNotFoundError(RuntimeError):
    pass


class RunNotWaitingForInputError(RuntimeError):
    pass


@dataclass
class RunContext:
    run: RunRecord
    runtime: AgentRuntime
    events: list[RunEvent] = field(default_factory=list)
    event_condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    frame_condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    latest_frame: bytes | None = None
    frame_sequence: int = 0
    next_event_sequence: int = 1
    execution_task: asyncio.Task[None] | None = None
    capture_task: asyncio.Task[None] | None = None
    assistant_output_parts: list[str] = field(default_factory=list)


class RunManager:
    FRAME_INTERVAL_SECONDS = 0.5

    def __init__(self, repository: RunRepository, headless: bool = True):
        self._repository = repository
        self._headless = headless
        self._contexts: dict[UUID, RunContext] = {}
        self._active_run_id: UUID | None = None
        self._manager_lock = asyncio.Lock()

    async def create_run(
        self,
        task: str,
        max_runtime_seconds: int,
    ) -> RunRecord:
        async with self._manager_lock:
            if self._active_run_id is not None:
                active_context = self._contexts.get(self._active_run_id)
                if (
                    active_context is not None
                    and not active_context.run.status.is_terminal
                ):
                    raise RunAlreadyActiveError("Another run is already active")

            run = RunRecord(
                task=task.strip(),
                max_runtime_seconds=max_runtime_seconds,
            )
            runtime = build_agent_runtime(run.thread_id, headless=self._headless)
            context = RunContext(run=run, runtime=runtime)
            self._contexts[run.id] = context
            self._active_run_id = run.id
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
            return run

    def get_run(self, run_id: UUID) -> RunRecord:
        context = self._contexts.get(run_id)
        if context is not None:
            return context.run

        run = self._repository.get_run(run_id)
        if run is None:
            raise RunNotFoundError(f"Run {run_id} was not found")

        return run

    async def submit_input(self, run_id: UUID, answer: str) -> RunRecord:
        context = self._contexts.get(run_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")

        if context.run.status != RunStatus.WAITING_FOR_INPUT:
            raise RunNotWaitingForInputError(
                "The run is not waiting for user input"
            )

        await self._publish(
            context,
            "input.received",
            {"answer": answer.strip()},
        )
        context.execution_task = asyncio.create_task(
            self._execute(context, Command(resume=answer.strip())),
            name=f"resume-{run_id}",
        )
        return context.run

    async def cancel_run(self, run_id: UUID) -> RunRecord:
        context = self._contexts.get(run_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")

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

    async def stream_events(
        self,
        run_id: UUID,
        after_sequence: int = 0,
    ) -> AsyncIterator[RunEvent]:
        context = self._contexts.get(run_id)
        if context is None:
            run = self._repository.get_run(run_id)
            if run is None:
                raise RunNotFoundError(f"Run {run_id} was not found")

            stored_events = self._repository.list_events(run_id, after_sequence)
            for event in stored_events:
                yield event
            return

        next_sequence = after_sequence + 1
        while True:
            async with context.event_condition:
                available_events = [
                    event
                    for event in context.events
                    if event.sequence >= next_sequence
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

    async def stream_frames(self, run_id: UUID) -> AsyncIterator[bytes]:
        context = self._contexts.get(run_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")

        delivered_frame_sequence = 0
        while True:
            async with context.frame_condition:
                has_new_frame = (
                    context.latest_frame is not None
                    and context.frame_sequence > delivered_frame_sequence
                )
                while not has_new_frame and not context.run.status.is_terminal:
                    await context.frame_condition.wait()
                    has_new_frame = (
                        context.latest_frame is not None
                        and context.frame_sequence > delivered_frame_sequence
                    )

                is_terminal = context.run.status.is_terminal
                frame = context.latest_frame if has_new_frame else None
                if has_new_frame:
                    delivered_frame_sequence = context.frame_sequence

            if frame is not None:
                yield frame

            if is_terminal:
                return

    async def shutdown(self) -> None:
        contexts = list(self._contexts.values())
        for context in contexts:
            if context.execution_task is not None:
                context.execution_task.cancel()

            if context.capture_task is not None:
                context.capture_task.cancel()

            await context.runtime.browser.close()

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
            await self._publish(
                context,
                "run.completed",
                {"output": final_output},
            )
            await self._set_status(context, RunStatus.SUCCEEDED)
            await self._finish_context(context)
        except TimeoutError:
            await self._publish(
                context,
                "run.failed",
                {"error": "The run exceeded its execution time limit"},
            )
            await self._set_status(context, RunStatus.TIMED_OUT)
            await self._finish_context(context)
        except asyncio.CancelledError:
            await self._publish(context, "run.cancelled", {})
            await self._set_status(context, RunStatus.CANCELLED)
            await self._finish_context(context)
            raise
        except Exception as exception:
            context.run.error = str(exception)
            await self._publish(
                context,
                "run.failed",
                {"error": str(exception)},
            )
            await self._set_status(context, RunStatus.FAILED)
            await self._finish_context(context)

    async def _handle_stream_part(
        self,
        context: RunContext,
        stream_part: dict[str, Any],
    ) -> None:
        stream_type = stream_part.get("type")
        data = stream_part.get("data")

        if stream_type == "custom" and isinstance(data, dict):
            event_type = data.get("event_type")
            payload = data.get("payload", {})
            if isinstance(event_type, str) and isinstance(payload, dict):
                await self._publish(context, event_type, payload)
                if event_type == "input.required":
                    await self._set_status(
                        context,
                        RunStatus.WAITING_FOR_INPUT,
                    )
            return

        if stream_type == "messages":
            message_text = self._extract_stream_message(data)
            if message_text:
                context.assistant_output_parts.append(message_text)
                await self._publish(
                    context,
                    "agent.message.delta",
                    {"text": message_text},
                    persist=False,
                )
            return

        if stream_type == "updates" and self._contains_interrupt(data):
            if context.run.status != RunStatus.WAITING_FOR_INPUT:
                await self._set_status(context, RunStatus.WAITING_FOR_INPUT)

    async def _capture_frames(self, context: RunContext) -> None:
        try:
            while not context.run.status.is_terminal:
                frame = await context.runtime.browser.capture_frame()
                if frame is not None:
                    async with context.frame_condition:
                        context.latest_frame = frame
                        context.frame_sequence += 1
                        context.frame_condition.notify_all()

                await asyncio.sleep(self.FRAME_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _publish(
        self,
        context: RunContext,
        event_type: str,
        payload: dict[str, Any],
        persist: bool = True,
    ) -> RunEvent:
        event = RunEvent(
            run_id=context.run.id,
            sequence=context.next_event_sequence,
            event_type=event_type,
            payload=self._json_safe(payload),
        )
        context.next_event_sequence += 1
        context.events.append(event)

        if persist:
            self._repository.save_event(event)

        async with context.event_condition:
            context.event_condition.notify_all()

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
        await self._publish(
            context,
            "run.status",
            {"status": status.value},
        )

    async def _finish_context(self, context: RunContext) -> None:
        if context.capture_task is not None:
            context.capture_task.cancel()
            try:
                await context.capture_task
            except asyncio.CancelledError:
                pass

        await context.runtime.browser.close()

        async with self._manager_lock:
            if self._active_run_id == context.run.id:
                self._active_run_id = None

        async with context.event_condition:
            context.event_condition.notify_all()

        async with context.frame_condition:
            context.frame_condition.notify_all()

    async def _get_final_output(self, context: RunContext) -> str:
        try:
            state = await context.runtime.agent.aget_state(
                context.runtime.config
            )
            messages = state.values.get("messages", [])
            if messages:
                message_text = self._extract_message_content(
                    messages[-1].content
                )
                if message_text:
                    return message_text
        except Exception:
            pass

        return "".join(context.assistant_output_parts).strip()

    def _extract_stream_message(self, data: Any) -> str:
        if not isinstance(data, (tuple, list)) or not data:
            return ""

        message = data[0]
        message_type = getattr(message, "type", None)
        if message_type not in {"ai", "assistant"}:
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
