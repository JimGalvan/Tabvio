import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from tabvio.runs.runtime import build_agent_runtime
from tabvio.clock import utc_now
from tabvio.credentials.exceptions import CredentialNotFoundError
from tabvio.credentials.service import CredentialService
from tabvio.runs import constants
from tabvio.runs.exceptions import (
    RunCapacityReachedError,
    RunNotFoundError,
    RunNotReadyForFollowUpError,
    RunNotRerunnableError,
    RunNotWaitingForInputError,
    SensitiveInputNotPendingError,
)
from tabvio.browser.constants import FRAME_QUALITY, FRAME_QUALITY_TAKEOVER
from tabvio.runs.models import (
    BrowserControlEvent,
    RunContext,
    RunEvent,
    RunRecord,
    RunStatus,
)
from tabvio.runs.repository import RunRepository


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
        self._resuming_run_ids: set[UUID] = set()

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
                task=task.strip(),
                max_runtime_seconds=max_runtime_seconds,
                user_id=user_id,
                credential_ids=selected_credential_ids,
            )
            runtime = build_agent_runtime(
                run.thread_id,
                user_id=user_id,
                credential_ids=tuple(selected_credential_ids),
                credential_service=self._credential_service,
                headless=self._headless,
                run_id=run.id,
            )
            context = RunContext(run=run, runtime=runtime)
            self._contexts[run.id] = context
            self._active_run_ids.add(run.id)

            try:
                self._repository.save_run(run)
                await self._publish(context, "run.created", {"task": run.task, "status": run.status.value})
                context.capture_task = asyncio.create_task(self._capture_frames(context), name=f"capture-{run.id}")
                context.execution_task = asyncio.create_task(
                    self._execute(context, context.runtime.start_input(run.task)), name=f"run-{run.id}"
                )
            except Exception:
                self._contexts.pop(run.id, None)
                self._active_run_ids.discard(run.id)
                await runtime.browser.close()
                raise

            return run

    async def rerun_run(self, run_id: UUID, user_id: UUID) -> RunRecord:
        """Start a fresh run from a finished one, reusing its task and credentials.

        The original record is left untouched so history keeps both attempts.
        """
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
        context = self._require_waiting_context(run_id, user_id)
        sensitive_inputs = getattr(context.runtime, "sensitive_inputs", None)
        if sensitive_inputs is not None and sensitive_inputs.pending is not None:
            raise RunNotWaitingForInputError(
                "The run is waiting for a verification code"
            )

        self._resuming_run_ids.add(run_id)
        try:
            await self._publish(context, "input.received", {})
            context.execution_task = asyncio.create_task(
                self._execute(context, context.runtime.resume_input(answer.strip())), name=f"resume-{run_id}"
            )
            return context.run
        except Exception:
            self._resuming_run_ids.discard(run_id)
            raise

    async def submit_sensitive_input(
            self,
            run_id: UUID,
            user_id: UUID,
            request_id: UUID,
            code: str,
    ) -> RunRecord:
        context, _ = self._resolve_owned(run_id, user_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")
        if context.run.status != RunStatus.WAITING_FOR_INPUT:
            raise SensitiveInputNotPendingError(
                "The run is not waiting for sensitive input"
            )
        try:
            pending = context.runtime.sensitive_inputs.require(request_id)
        except ValueError as exception:
            raise SensitiveInputNotPendingError(str(exception)) from exception

        self._cancel_sensitive_input_timeout(context)
        submitted = await context.runtime.browser.fill_sensitive(
            pending.element_index, code, pending.submit_element_index
        )
        self._resuming_run_ids.add(run_id)
        try:
            await self._publish(
                context,
                "sensitive_input.received",
                {"request_id": str(request_id), "kind": pending.kind},
            )
            context.execution_task = asyncio.create_task(
                self._execute(
                    context, context.runtime.resume_input({"entered": True, "submitted": submitted})
                ),
                name=f"sensitive-resume-{run_id}",
            )
            return context.run
        except Exception:
            self._resuming_run_ids.discard(run_id)
            raise

    async def decline_sensitive_input(self, run_id: UUID, user_id: UUID) -> RunRecord:
        """Drop a code request the person cannot answer and let the agent replan."""
        context, _ = self._resolve_owned(run_id, user_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")

        channel = self._sensitive_input_channel(context)
        if channel is None or channel.pending is None:
            raise SensitiveInputNotPendingError(
                "The run is not waiting for a verification code"
            )

        await self._withdraw_sensitive_input(
            context, constants.SENSITIVE_INPUT_DECLINED_REASON
        )
        return await self._resume_without_code(context)

    @staticmethod
    def _sensitive_input_channel(context: RunContext):
        return getattr(context.runtime, "sensitive_inputs", None)

    async def _withdraw_sensitive_input(self, context: RunContext, reason: str) -> None:
        """Take the code box off the screen. The agent step stays parked."""
        channel = self._sensitive_input_channel(context)
        if channel is None:
            return

        withdrawn = channel.withdraw(reason)
        if withdrawn is None:
            return

        self._cancel_sensitive_input_timeout(context)
        await self._publish(
            context,
            "sensitive_input.cancelled",
            {"request_id": str(withdrawn.id), "reason": reason},
        )

    async def _resume_without_code(self, context: RunContext) -> RunRecord:
        """Restart the parked agent step, telling it why no code arrived."""
        channel = self._sensitive_input_channel(context)
        reason = channel.take_withdrawn_reason() if channel is not None else None
        if reason is None:
            return context.run

        run_id = context.run.id
        self._resuming_run_ids.add(run_id)
        try:
            context.execution_task = asyncio.create_task(
                self._execute(
                    context, context.runtime.resume_input({"entered": False, "reason": reason})
                ),
                name=f"sensitive-decline-{run_id}",
            )
            return context.run
        except Exception:
            self._resuming_run_ids.discard(run_id)
            raise

    def _start_sensitive_input_timeout(self, context: RunContext) -> datetime:
        """Give the person a bounded window to produce a code."""
        self._cancel_sensitive_input_timeout(context)
        context.sensitive_input_timeout_task = asyncio.create_task(
            self._expire_sensitive_input(context),
            name=f"sensitive-timeout-{context.run.id}",
        )
        return utc_now() + timedelta(seconds=self._sensitive_input_window_seconds)

    def _cancel_sensitive_input_timeout(self, context: RunContext) -> None:
        timeout_task = context.sensitive_input_timeout_task
        context.sensitive_input_timeout_task = None
        if timeout_task is not None and timeout_task is not asyncio.current_task():
            timeout_task.cancel()

    async def _expire_sensitive_input(self, context: RunContext) -> None:
        try:
            await asyncio.sleep(self._sensitive_input_window_seconds)
        except asyncio.CancelledError:
            return

        if self._contexts.get(context.run.id) is not context:
            return
        if context.run.status != RunStatus.WAITING_FOR_INPUT:
            return

        channel = self._sensitive_input_channel(context)
        if channel is None or channel.pending is None:
            return

        context.sensitive_input_timeout_task = None
        await self._withdraw_sensitive_input(
            context, constants.SENSITIVE_INPUT_TIMEOUT_REASON
        )
        await self._resume_without_code(context)

    def _require_waiting_context(self, run_id: UUID, user_id: UUID) -> RunContext:
        """The live context of a run this account owns that is parked on input."""
        context, _ = self._resolve_owned(run_id, user_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")

        if context.run.status != RunStatus.WAITING_FOR_INPUT:
            raise RunNotWaitingForInputError("The run is not waiting for user input")

        return context

    async def open_control(self, run_id: UUID, user_id: UUID) -> RunContext:
        """Hand the browser to the person watching it.

        A pending verification code is withdrawn first: it holds an element index
        that a person clicking around would break. The agent is told the code
        never arrived once control goes back.
        """
        context, _ = self._resolve_owned(run_id, user_id)
        if context is None:
            raise RunNotFoundError(f"Run {run_id} was not found")
        self._require_control_status(context)

        await self._withdraw_sensitive_input(
            context, constants.SENSITIVE_INPUT_TAKEOVER_REASON
        )

        context.controller_count += 1
        if context.controller_count == 1:
            await self._publish(context, "takeover.started", {})
        if context.capture_task is None or context.capture_task.done():
            context.capture_task = asyncio.create_task(
                self._capture_frames(context), name=f"capture-{run_id}"
            )

        return context

    async def close_control(self, context: RunContext, controller_id: str = "default") -> None:
        async with context.control_lock:
            if context.mouse_controller == controller_id:
                await self._release_control_mouse(context)
        context.controller_count = max(context.controller_count - 1, 0)
        if context.controller_count == 0 and not context.run.status.is_terminal:
            await self._publish(context, "takeover.ended", {})
            if context.run.status == RunStatus.READY_FOR_FOLLOW_UP:
                await self._pause_frame_capture(context)
            await self._resume_without_code(context)

    @staticmethod
    async def _release_control_mouse(context: RunContext) -> None:
        if context.mouse_controller is not None:
            try:
                await context.runtime.browser.user_mouse_up()
            except Exception:
                logger.exception("Could not release the mouse for run %s", context.run.id)
            finally:
                context.mouse_controller = None

    @staticmethod
    def _require_control_status(context: RunContext) -> None:
        if context.run.status not in {
            RunStatus.WAITING_FOR_INPUT, RunStatus.READY_FOR_FOLLOW_UP,
        }:
            raise RunNotWaitingForInputError("The browser is not available for user control")

    async def apply_control(
            self,
            context: RunContext,
            event: BrowserControlEvent,
            controller_id: str = "default",
    ) -> str:
        """Play one of a person's actions into the browser.

        Serialised per run so that two open dashboards cannot interleave a
        click and a keystroke into the same page.
        """
        browser = context.runtime.browser
        async with context.control_lock:
            if event.type == "mouse_up":
                if context.mouse_controller == controller_id:
                    await self._release_control_mouse(context)
                return "Mouse button released"
            self._require_control_status(context)
            if context.mouse_controller is not None and event.type in {"click", "mouse_down", "scroll"}:
                raise RunNotWaitingForInputError("Release the held mouse button before another action")
            if event.type == "mouse_down":
                context.mouse_controller = controller_id
                try:
                    return await browser.user_mouse_down(event.x, event.y)
                except Exception:
                    await self._release_control_mouse(context)
                    raise
            if event.type == "click":
                return await browser.user_click(event.x, event.y)
            if event.type == "scroll":
                return await browser.user_scroll(event.x, event.y, event.delta_y)
            if event.type == "key":
                return await browser.user_key(event.key)
            return await browser.user_text(event.text)

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
            if context.capture_task is None or context.capture_task.done():
                context.capture_task = asyncio.create_task(self._capture_frames(context), name=f"capture-{run_id}")
            context.execution_task = asyncio.create_task(
                self._execute(context, context.runtime.start_input(follow_up_task))
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
            self._cancel_sensitive_input_timeout(context)
            await context.runtime.browser.close()
        self._contexts.clear()
        self._active_run_ids.clear()
        self._completed_frames.clear()
        self._resuming_run_ids.clear()

    async def _execute(self, context: RunContext, agent_input: Any) -> None:
        await self._set_status(context, RunStatus.RUNNING)

        try:
            async with asyncio.timeout(context.run.max_runtime_seconds):
                async for stream_item in context.runtime.stream(agent_input):
                    await self._handle_stream_item(context, stream_item)

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
        failing_since = None

        while not context.run.status.is_terminal:
            taken_over = context.controller_count > 0
            quality = FRAME_QUALITY_TAKEOVER if taken_over else FRAME_QUALITY
            try:
                frame = await asyncio.wait_for(context.runtime.browser.capture_screen_frame(quality),
                                               timeout=constants.FRAME_CAPTURE_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exception:
                logger.warning("Browser frame capture failed for run %s: %s", context.run.id, exception)
                if failing_since is None:
                    failing_since = time.monotonic()

                outage_seconds = time.monotonic() - failing_since
                if not capture_failure_active and outage_seconds >= constants.FRAME_CAPTURE_GRACE_SECONDS:
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

                failing_since = None
                if capture_failure_active:
                    capture_failure_active = False
                    await self._publish(context, "browser.capture.recovered",
                                        {"message": "Live view resumed"})

            await asyncio.sleep(
                constants.FRAME_INTERVAL_SECONDS_TAKEOVER
                if taken_over
                else constants.FRAME_INTERVAL_SECONDS
            )

    async def _finish_context(self, context: RunContext) -> None:
        self._cancel_sensitive_input_timeout(context)
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

        async with context.control_lock:
            if status not in {RunStatus.WAITING_FOR_INPUT, RunStatus.READY_FOR_FOLLOW_UP}:
                await self._release_control_mouse(context)
            context.run.status = status
        context.run.updated_at = utc_now()
        self._repository.save_run(context.run)
        status_payload = {"status": status.value}
        if context.run.follow_up_expires_at is not None:
            status_payload["follow_up_expires_at"] = context.run.follow_up_expires_at.isoformat()
        await self._publish(context, "run.status", status_payload)

    async def _handle_stream_item(
            self,
            context: RunContext,
            stream_item: dict[str, Any],
    ) -> None:
        kind = stream_item.get("kind")

        if kind == "custom":
            await self._handle_custom_event(context, stream_item)
            return

        if kind == "message":
            message_text = stream_item.get("text", "")
            if message_text:
                context.assistant_output_parts.append(message_text)
                await self._publish(context, "agent.message.delta",
                                    {"text": message_text}, persist=False)
                self._trim_assistant_output(context)
            return

        if kind == "interrupt" and context.run.status != RunStatus.WAITING_FOR_INPUT:
            await self._set_status(context, RunStatus.WAITING_FOR_INPUT)

    async def _handle_custom_event(
            self,
            context: RunContext,
            stream_item: dict[str, Any],
    ) -> None:
        event_type = stream_item.get("event_type")
        if (
                event_type in {"input.required", "sensitive_input.required"}
                and context.run.id in self._resuming_run_ids
        ):
            self._resuming_run_ids.discard(context.run.id)
            return

        payload = stream_item.get("payload", {})
        if not isinstance(event_type, str) or not isinstance(payload, dict):
            return

        if event_type == "sensitive_input.required":
            deadline = self._start_sensitive_input_timeout(context)
            payload = {**payload, "expires_at": deadline.isoformat()}

        await self._publish(context, event_type, payload)
        if event_type in {"input.required", "sensitive_input.required"}:
            await self._set_status(context, RunStatus.WAITING_FOR_INPUT)

    async def _get_final_output(self, context: RunContext) -> str:
        message_text = await context.runtime.final_output()
        if message_text:
            return message_text

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
