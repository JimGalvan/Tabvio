import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID

from tabvio_agent.events import BrowserControlAppliedEvent, SessionStatusEvent
from tabvio_agent.exceptions import (
    SensitiveInputNotPendingError as SessionSensitiveInputNotPendingError,
)
from tabvio_agent.exceptions import (
    SessionNotReadyForFollowUpError,
    SessionNotWaitingForInputError,
)
from tabvio_agent.models import MouseUpAction

from tabvio.clock import utc_now
from tabvio.credentials.exceptions import CredentialNotFoundError
from tabvio.credentials.service import CredentialService
from tabvio.runs import constants
from tabvio.runs.event_utils import RunEventUtils
from tabvio.runs.exceptions import (
    RunCapacityReachedError,
    RunNotFoundError,
    RunNotReadyForFollowUpError,
    RunNotRerunnableError,
    RunNotWaitingForInputError,
    SensitiveInputNotPendingError,
)
from tabvio.runs.models import (
    BrowserControlEvent,
    RunContext,
    RunEvent,
    RunRecord,
    RunStatus,
)
from tabvio.runs.repository import RunRepository
from tabvio.runs.runtime import build_agent_runtime


class RunManager:
    def __init__(
            self,
            repository: RunRepository,
            headless: bool = True,
            max_concurrent_runs: int = constants.DEFAULT_MAX_CONCURRENT_RUNS,
            follow_up_window_seconds: float = constants.DEFAULT_FOLLOW_UP_WINDOW_SECONDS,
            credential_service: CredentialService | None = None,
            sensitive_input_window_seconds: float = constants.DEFAULT_SENSITIVE_INPUT_WINDOW_SECONDS,
    ):
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be at least 1")
        if follow_up_window_seconds <= 0:
            raise ValueError("follow_up_window_seconds must be greater than 0")
        if sensitive_input_window_seconds <= 0:
            raise ValueError("sensitive_input_window_seconds must be greater than 0")
        self._repository = repository
        self._headless = headless
        self._contexts: dict[UUID, RunContext] = {}
        self._manager_lock = asyncio.Lock()
        self._max_concurrent_runs = max_concurrent_runs
        self._follow_up_window_seconds = follow_up_window_seconds
        self._sensitive_input_window_seconds = sensitive_input_window_seconds
        self._credential_service = credential_service
        self._active_run_ids: set[UUID] = set()
        self._completed_frames: OrderedDict[UUID, bytes] = OrderedDict()

    async def create_run(
            self,
            task: str,
            max_runtime_seconds: int,
            user_id: UUID | None = None,
            credential_ids: list[UUID] | None = None,
    ) -> RunRecord:
        async with self._manager_lock:
            if len(self._active_run_ids) >= self._max_concurrent_runs:
                raise RunCapacityReachedError("The demo is currently at capacity. Try again shortly.")
            selected_credential_ids = list(dict.fromkeys(credential_ids or []))
            if selected_credential_ids:
                if user_id is None or self._credential_service is None:
                    raise ValueError("Credential selection requires an authenticated user")
                self._credential_service.require_selected(selected_credential_ids, user_id)
            run = RunRecord(
                task=task.strip(), max_runtime_seconds=max_runtime_seconds,
                user_id=user_id, credential_ids=selected_credential_ids,
            )
            session = build_agent_runtime(
                run.thread_id,
                user_id=user_id,
                credential_ids=tuple(selected_credential_ids),
                credential_service=self._credential_service,
                headless=self._headless,
                max_runtime_seconds=max_runtime_seconds,
                sensitive_input_window_seconds=self._sensitive_input_window_seconds,
            )
            context = RunContext(run=run, session=session)
            self._contexts[run.id] = context
            self._active_run_ids.add(run.id)
            try:
                self._repository.save_run(run)
                await self._publish(context, "run.created", {"task": run.task, "status": run.status.value})
                context.event_task = asyncio.create_task(self._forward_events(context), name=f"events-{run.id}")
                context.frame_task = asyncio.create_task(self._forward_frames(context), name=f"frames-{run.id}")
                await session.start(run.task)
            except Exception:
                self._contexts.pop(run.id, None)
                self._active_run_ids.discard(run.id)
                await session.close()
                await self._stop_task(context.event_task)
                await self._stop_task(context.frame_task)
                raise
            return run

    async def submit_input(self, run_id: UUID, user_id: UUID, answer: str) -> RunRecord:
        context = self._require_waiting_context(run_id, user_id)
        try:
            await context.session.answer(answer)
        except SessionNotWaitingForInputError as exception:
            raise RunNotWaitingForInputError(str(exception)) from exception
        return context.run

    async def submit_sensitive_input(
            self, run_id: UUID, user_id: UUID, request_id: UUID, code: str,
    ) -> RunRecord:
        context, _ = self._resolve_owned(run_id, user_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")
        if context.run.status != RunStatus.WAITING_FOR_INPUT:
            raise SensitiveInputNotPendingError("The run is not waiting for sensitive input")
        try:
            await context.session.submit_sensitive_input(request_id, code)
        except SessionSensitiveInputNotPendingError as exception:
            raise SensitiveInputNotPendingError(str(exception)) from exception
        return context.run

    async def decline_sensitive_input(self, run_id: UUID, user_id: UUID) -> RunRecord:
        context, _ = self._resolve_owned(run_id, user_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")
        if context.sensitive_input_request_id is None:
            raise SensitiveInputNotPendingError("The run is not waiting for a verification code")
        try:
            await context.session.decline_sensitive_input(context.sensitive_input_request_id)
        except SessionSensitiveInputNotPendingError as exception:
            raise SensitiveInputNotPendingError(str(exception)) from exception
        return context.run

    async def open_control(self, run_id: UUID, user_id: UUID) -> RunContext:
        context, _ = self._resolve_owned(run_id, user_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")
        self._require_control_status(context)
        try:
            await context.session.take_control()
        except SessionNotWaitingForInputError as exception:
            raise RunNotWaitingForInputError(str(exception)) from exception
        return context

    async def close_control(self, context: RunContext, controller_id: str = "default") -> None:
        async with context.control_lock:
            if context.mouse_controller == controller_id:
                if not context.run.status.is_terminal:
                    await context.session.apply_control(MouseUpAction())
                    await context.control_results.get()
                context.mouse_controller = None
            await context.session.release_control()

    @staticmethod
    def _require_control_status(context: RunContext) -> None:
        if context.run.status not in {RunStatus.WAITING_FOR_INPUT, RunStatus.READY_FOR_FOLLOW_UP}:
            raise RunNotWaitingForInputError("The browser is not available for user control")

    async def apply_control(
            self, context: RunContext, event: BrowserControlEvent, controller_id: str = "default",
    ) -> str:
        async with context.control_lock:
            if event.type == "mouse_up" and context.mouse_controller != controller_id:
                return "Mouse button released"
            if event.type != "mouse_up":
                self._require_control_status(context)
            if context.mouse_controller and event.type in {"click", "mouse_down", "scroll"}:
                raise RunNotWaitingForInputError("Release the held mouse button before another action")
            try:
                await context.session.apply_control(RunEventUtils.control_action(event))
            except SessionNotWaitingForInputError as exception:
                raise RunNotWaitingForInputError(str(exception)) from exception
            if event.type == "mouse_down" and context.run.status in {
                RunStatus.WAITING_FOR_INPUT, RunStatus.READY_FOR_FOLLOW_UP,
            }:
                context.mouse_controller = controller_id
            elif event.type == "mouse_up":
                context.mouse_controller = None
            return await context.control_results.get()

    async def submit_follow_up(self, run_id: UUID, user_id: UUID, task: str) -> RunRecord:
        async with self._manager_lock:
            context, _ = self._resolve_owned(run_id, user_id)
            if context is None:
                raise RunNotFoundError(f"Run {run_id} was not found")
            if context.run.status != RunStatus.READY_FOR_FOLLOW_UP:
                raise RunNotReadyForFollowUpError("The run is not ready for a follow-up")
            expiry_task = context.follow_up_expiry_task
            context.follow_up_expiry_task = None
            if expiry_task:
                expiry_task.cancel()
            context.run.follow_up_expires_at = None
            context.run.final_output = None
            context.run.error = None
            follow_up_task = task.strip()
            await self._publish(context, "follow_up.started", {"task": follow_up_task})
            try:
                await context.session.follow_up(follow_up_task)
            except SessionNotReadyForFollowUpError as exception:
                raise RunNotReadyForFollowUpError(str(exception)) from exception
            await self._set_status(context, RunStatus.RUNNING)
        await self._await_cancelled_task(expiry_task)
        return context.run

    async def cancel_run(self, run_id: UUID, user_id: UUID) -> RunRecord:
        context, run = self._resolve_owned(run_id, user_id)
        if context is None:
            if not run.status.is_terminal:
                raise RunNotFoundError(f"Run {run_id} was not found")
            return run
        if context.run.status.is_terminal:
            return context.run
        await context.session.cancel()
        await self._await_cancelled_task(context.event_task)
        return context.run

    async def shutdown(self) -> None:
        contexts = list(self._contexts.values())
        for context in contexts:
            expiry_task = context.follow_up_expiry_task
            context.follow_up_expiry_task = None
            await self._stop_task(expiry_task)
            await context.session.close()
            await self._await_cancelled_task(context.event_task)
            await self._stop_task(context.frame_task)
        self._contexts.clear()
        self._active_run_ids.clear()
        self._completed_frames.clear()

    async def _forward_events(self, context: RunContext) -> None:
        try:
            async for event in context.session.events():
                if isinstance(event, BrowserControlAppliedEvent):
                    await context.control_results.put(event.detail)
                    continue
                if isinstance(event, SessionStatusEvent):
                    status = RunStatus(event.status)
                    if status == RunStatus.TIMED_OUT:
                        context.run.error = None
                    await self._set_status(context, status)
                    if status == RunStatus.READY_FOR_FOLLOW_UP:
                        context.follow_up_expiry_task = asyncio.create_task(
                            self._expire_follow_up_window(context), name=f"follow-up-expiry-{context.run.id}",
                        )
                    elif status.is_terminal:
                        await self._finish_context(context)
                    continue
                payload = RunEventUtils.payload(event)
                if event.event_type == "run.completed":
                    context.run.final_output = event.output
                    context.run.follow_up_expires_at = utc_now() + timedelta(seconds=self._follow_up_window_seconds)
                    payload["follow_up_expires_at"] = context.run.follow_up_expires_at.isoformat()
                elif event.event_type == "run.failed":
                    context.run.error = event.error
                    context.run.follow_up_expires_at = None
                elif event.event_type == "run.cancelled":
                    context.run.follow_up_expires_at = None
                elif event.event_type == "sensitive_input.required":
                    context.sensitive_input_request_id = event.request_id
                elif event.event_type in {"sensitive_input.received", "sensitive_input.cancelled"}:
                    context.sensitive_input_request_id = None
                await self._publish(context, event.event_type, payload, persist=event.event_type != "agent.message.delta")
        except Exception as exception:
            context.run.error = str(exception)
            context.run.follow_up_expires_at = None
            try:
                await self._publish(context, "run.failed", {"error": str(exception)})
                await self._set_status(context, RunStatus.FAILED)
            finally:
                await self._finish_context(context)

    async def _forward_frames(self, context: RunContext) -> None:
        async for frame in context.session.screen_frames():
            async with context.frame_condition:
                context.latest_frame = frame
                context.frame_sequence += 1
                context.frame_condition.notify_all()

    async def _finish_context(self, context: RunContext) -> None:
        expiry_task = context.follow_up_expiry_task
        context.follow_up_expiry_task = None
        await self._stop_task(expiry_task)
        await context.session.close()
        await self._await_cancelled_task(context.event_task)
        await self._await_cancelled_task(context.frame_task)
        latest_frame = context.session.get_latest_frame()
        if latest_frame is not None:
            context.latest_frame = latest_frame
        if context.latest_frame is not None:
            self._completed_frames[context.run.id] = context.latest_frame
            self._completed_frames.move_to_end(context.run.id)
            while len(self._completed_frames) > constants.MAX_COMPLETED_FRAME_COUNT:
                self._completed_frames.popitem(last=False)
        async with self._manager_lock:
            self._active_run_ids.discard(context.run.id)
            self._contexts.pop(context.run.id, None)
        async with context.event_condition:
            context.event_condition.notify_all()
        async with context.frame_condition:
            context.frame_condition.notify_all()

    async def _set_status(self, context: RunContext, status: RunStatus) -> None:
        if context.run.status == status:
            return
        if status not in {RunStatus.WAITING_FOR_INPUT, RunStatus.READY_FOR_FOLLOW_UP}:
            context.mouse_controller = None
        context.run.status = status
        context.run.updated_at = utc_now()
        self._repository.save_run(context.run)
        payload = {"status": status.value}
        if context.run.follow_up_expires_at is not None:
            payload["follow_up_expires_at"] = context.run.follow_up_expires_at.isoformat()
        await self._publish(context, "run.status", payload)

    async def _stop_task(self, task: asyncio.Task[None] | None) -> None:
        if task and task is not asyncio.current_task():
            task.cancel()
            await self._await_cancelled_task(task)

    async def rerun_run(self, run_id: UUID, user_id: UUID) -> RunRecord:
        _, run = self._resolve_owned(run_id, user_id)
        if not run.status.is_terminal:
            raise RunNotRerunnableError("Only a finished run can be started again")

        try:
            return await self.create_run(
                task=run.task,
                max_runtime_seconds=run.max_runtime_seconds,
                user_id=user_id,
                credential_ids=list(run.credential_ids),
            )
        except CredentialNotFoundError as exception:
            raise RunNotRerunnableError(
                "A credential this run used is no longer available"
            ) from exception

    def list_runs(
            self,
            user_id: UUID,
            limit: int = constants.MAX_LISTED_RUNS,
    ) -> list[RunRecord]:
        stored_runs = self._repository.list_runs_for_user(user_id, limit)
        runs = []
        for run in stored_runs:
            context = self._contexts.get(run.id)
            runs.append(context.run if context else run)
        return runs

    def _resolve_owned(
            self,
            run_id: UUID,
            user_id: UUID,
    ) -> tuple[RunContext | None, RunRecord]:
        context = self._contexts.get(run_id)
        run = context.run if context is not None else self._repository.get_run(run_id)
        if run is None or run.user_id != user_id:
            raise RunNotFoundError(f"Run {run_id} was not found")

        return context, run

    def get_run(self, run_id: UUID, user_id: UUID) -> RunRecord:
        _, run = self._resolve_owned(run_id, user_id)
        return run

    def _require_waiting_context(self, run_id: UUID, user_id: UUID) -> RunContext:
        context, _ = self._resolve_owned(run_id, user_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")

        if context.run.status != RunStatus.WAITING_FOR_INPUT:
            raise RunNotWaitingForInputError("The run is not waiting for user input")

        return context

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
                available_events = []
                for event in context.events:
                    if event.sequence >= next_sequence:
                        available_events.append(event)
                while not available_events and not context.run.status.is_terminal:
                    await context.event_condition.wait()
                    available_events = []
                    for event in context.events:
                        if event.sequence >= next_sequence:
                            available_events.append(event)

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

    async def _publish(
            self,
            context: RunContext,
            event_type: str,
            payload: dict[str, Any],
            persist: bool = True,
    ) -> RunEvent:
        event = RunEvent(run_id=context.run.id, sequence=context.next_event_sequence, event_type=event_type,
                         payload=RunEventUtils.json_safe(payload))
        context.next_event_sequence += 1
        context.events.append(event)

        if persist:
            self._repository.save_event(event)

        async with context.event_condition:
            context.event_condition.notify_all()

        if event_type == "agent.message.delta":
            self._trim_live_message_events(context)

        return event

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


__all__ = ["RunManager"]
