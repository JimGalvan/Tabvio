import asyncio
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4

from tabvio_agent import constants
from tabvio_agent.browser.constants import FRAME_QUALITY, FRAME_QUALITY_TAKEOVER
from tabvio_agent.browser.session import BrowserSession
from tabvio_agent.browser_agent.browser_agent import build_browser_agent
from tabvio_agent.browser_agent.context import AgentContext
from tabvio_agent.credentials import CredentialProvider, SelectedCredentialProvider
from tabvio_agent.event_utils import EventUtils
from tabvio_agent.events import (
    AgentEvent,
    AgentMessageDeltaEvent,
    BrowserCaptureFailedEvent,
    BrowserCaptureRecoveredEvent,
    BrowserControlAppliedEvent,
    InputReceivedEvent,
    RunCancelledEvent,
    RunCompletedEvent,
    RunFailedEvent,
    SensitiveInputCancelledEvent,
    SensitiveInputReceivedEvent,
    SessionStatusEvent,
    TakeoverEndedEvent,
    TakeoverStartedEvent,
)
from tabvio_agent.exceptions import (
    SensitiveInputNotPendingError,
    SessionNotReadyForFollowUpError,
    SessionNotWaitingForInputError,
)
from tabvio_agent.models import BrowserControlAction, SessionStatus
from tabvio_agent.runtime import LangChainAgentRuntime
from tabvio_agent.sensitive_input import SensitiveInputChannel
from tabvio_agent.settings import AgentSettings


class BrowserAgentSession:
    def __init__(
            self,
            settings: AgentSettings,
            *,
            credential_provider: CredentialProvider | None = None,
            selected_credential_ids: tuple[UUID, ...] = (),
    ) -> None:
        if settings.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be greater than 0")
        if settings.sensitive_input_window_seconds <= 0:
            raise ValueError("sensitive_input_window_seconds must be greater than 0")
        self._settings = settings
        self._browser = BrowserSession(
            headless=settings.headless,
            trace_path=settings.trace_path,
            browser_channel=settings.browser_channel,
        )
        self._sensitive_inputs = SensitiveInputChannel()
        if credential_provider:
            credential_provider = SelectedCredentialProvider(credential_provider, selected_credential_ids)
        agent = build_browser_agent(
            self._browser, self._sensitive_inputs, settings, credential_provider=credential_provider,
        )
        self._runtime = LangChainAgentRuntime(
            agent, self._browser, AgentContext(), self._sensitive_inputs, uuid4(),
        )
        self._event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self._frame_condition = asyncio.Condition()
        self._latest_frame: bytes | None = None
        self._frame_sequence = 0
        self._execution_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._capture_task: asyncio.Task[None] | None = None
        self._sensitive_input_timeout_task: asyncio.Task[None] | None = None
        self._assistant_output_parts: list[str] = []
        self._controller_count = 0
        self._mouse_down = False
        self._control_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._status: SessionStatus | None = None
        self._started = False
        self._resuming = False
        self._terminal = False
        self._terminating = False
        self._closed = False

    async def start(self, task: str) -> None:
        async with self._command_lock:
            if self._started or self._closed:
                raise RuntimeError("The session has already been started or closed")
            self._started = True
            await self._start_turn(self._runtime.start_input(task.strip()))

    async def follow_up(self, task: str) -> None:
        async with self._command_lock:
            if self._closed or self._status != "ready_for_follow_up":
                raise SessionNotReadyForFollowUpError("The run is not ready for a follow-up")
            await self._await_task(self._execution_task)
            self._assistant_output_parts = []
            await self._start_turn(self._runtime.start_input(task.strip()))

    async def answer(self, text: str) -> None:
        async with self._command_lock:
            self._require_waiting()
            if self._sensitive_inputs.pending or self._sensitive_inputs.is_withdrawn:
                raise SessionNotWaitingForInputError("The run is waiting for a verification code")
            await self._await_task(self._execution_task)
            self._resuming = True
            await self._emit(InputReceivedEvent())
            await self._start_turn(self._runtime.resume_input(text.strip()))

    async def submit_sensitive_input(self, request_id: UUID, value: str) -> None:
        async with self._command_lock:
            if self._closed or self._status != "waiting_for_input":
                raise SensitiveInputNotPendingError("The run is not waiting for sensitive input")
            try:
                pending = self._sensitive_inputs.require(request_id)
            except ValueError as exception:
                raise SensitiveInputNotPendingError(str(exception)) from exception
            await self._await_task(self._execution_task)
            self._cancel_sensitive_input_timeout()
            submitted = await self._browser.fill_sensitive(
                pending.element_index, value, pending.submit_element_index,
            )
            self._resuming = True
            await self._emit(SensitiveInputReceivedEvent(request_id, pending.kind))
            await self._start_turn(self._runtime.resume_input({"entered": True, "submitted": submitted}))

    async def decline_sensitive_input(self, request_id: UUID) -> None:
        async with self._command_lock:
            if self._closed or self._status != "waiting_for_input" or not self._sensitive_inputs.pending:
                raise SensitiveInputNotPendingError("The run is not waiting for a verification code")
            try:
                self._sensitive_inputs.require(request_id)
            except ValueError as exception:
                raise SensitiveInputNotPendingError(str(exception)) from exception
            await self._withdraw_sensitive_input(constants.SENSITIVE_INPUT_DECLINED_REASON)
            await self._resume_without_code()

    async def take_control(self) -> None:
        async with self._command_lock:
            self._require_control_status()
            await self._withdraw_sensitive_input(constants.SENSITIVE_INPUT_TAKEOVER_REASON)
            self._controller_count += 1
            if self._controller_count == 1:
                await self._emit(TakeoverStartedEvent())
            self._start_frame_capture()

    async def apply_control(self, action: BrowserControlAction) -> str:
        async with self._control_lock:
            if action.type == "mouse_up":
                await self._release_control_mouse()
                detail = "Mouse button released"
            else:
                self._require_control_status()
                if self._mouse_down and action.type in {"click", "mouse_down", "scroll"}:
                    raise SessionNotWaitingForInputError("Release the held mouse button before another action")
                detail = await self._apply_control(action)
            await self._emit(BrowserControlAppliedEvent(detail))
            return detail

    async def _apply_control(self, action: BrowserControlAction) -> str:
        if action.type == "mouse_down":
            self._mouse_down = True
            try:
                return await self._browser.user_mouse_down(action.x, action.y)
            except Exception:
                await self._release_control_mouse()
                raise
        if action.type == "click":
            return await self._browser.user_click(action.x, action.y)
        if action.type == "scroll":
            return await self._browser.user_scroll(action.x, action.y, action.delta_y)
        if action.type == "key":
            return await self._browser.user_key(action.key)
        return await self._browser.user_text(action.text)

    async def release_control(self) -> None:
        async with self._command_lock:
            self._controller_count = max(self._controller_count - 1, 0)
            if self._controller_count == 0:
                async with self._control_lock:
                    await self._release_control_mouse()
                if not self._terminal and not self._closed:
                    await self._emit(TakeoverEndedEvent())
                    if self._status == "ready_for_follow_up":
                        await self._pause_frame_capture()
                    await self._resume_without_code()

    def screen_frames(self) -> AsyncIterator[bytes]:
        return self._screen_frames()

    async def _screen_frames(self) -> AsyncIterator[bytes]:
        delivered_sequence = 0
        while True:
            async with self._frame_condition:
                while self._frame_sequence <= delivered_sequence and not self._closed:
                    await self._frame_condition.wait()
                frame = None
                if self._frame_sequence > delivered_sequence:
                    frame = self._latest_frame
                    delivered_sequence = self._frame_sequence
                closed = self._closed
            if frame is not None:
                yield frame
            if closed:
                return

    def get_latest_frame(self) -> bytes | None:
        return self._latest_frame

    def events(self) -> AsyncIterator[AgentEvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[AgentEvent]:
        while not self._closed or not self._event_queue.empty():
            event = await self._event_queue.get()
            if event is None:
                return
            yield event

    async def cancel(self) -> None:
        await self._terminate("cancelled", RunCancelledEvent())

    async def _terminate(self, status: SessionStatus | None = None, event: AgentEvent | None = None) -> None:
        if not self._terminating:
            self._terminating = True
            execution_task = self._execution_task
            if execution_task and execution_task is not asyncio.current_task() and not execution_task.done():
                execution_task.cancel()
            if status and event:
                self._terminal = True
                await self._emit(event)
                await self._publish_status(status)
        await self._finish_resources()
        await self._await_task(self._execution_task)

    async def close(self) -> None:
        if self._status == "ready_for_follow_up" or self._execution_task is None:
            await self._terminate()
        else:
            await self.cancel()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def _start_turn(self, agent_input: Any) -> None:
        if self._terminating:
            raise RuntimeError("The session has already been closed")
        prepared = asyncio.Event()
        self._execution_task = asyncio.create_task(self._execute(agent_input, prepared))
        await self._set_status("running")
        if self._terminating:
            return
        self._start_frame_capture()
        prepared.set()

    async def _execute(self, agent_input: Any, prepared: asyncio.Event) -> None:
        try:
            await prepared.wait()
            if self._terminating:
                return
            async with asyncio.timeout(self._settings.max_runtime_seconds):
                async for stream_item in self._runtime.stream(agent_input):
                    await self._handle_stream_item(stream_item)
            if self._status == "waiting_for_input":
                return
            final_output = await self._runtime.final_output()
            if not final_output:
                final_output = "".join(self._assistant_output_parts).strip()
            await self._emit(RunCompletedEvent(final_output))
            await self._set_status("ready_for_follow_up")
            await self._pause_frame_capture()
        except TimeoutError:
            await self._terminate("timed_out", RunFailedEvent(constants.EXECUTION_TIMEOUT_ERROR))
        except asyncio.CancelledError:
            await self._terminate("cancelled", RunCancelledEvent())
        except Exception as exception:
            await self._terminate("failed", RunFailedEvent(str(exception)))
        finally:
            self._resuming = False

    async def _handle_stream_item(self, stream_item: dict[str, Any]) -> None:
        kind = stream_item.get("kind")
        if kind == "custom":
            await self._handle_custom_event(stream_item)
        elif kind == "message":
            text = stream_item.get("text", "")
            if text:
                self._assistant_output_parts.append(text)
                await self._emit(AgentMessageDeltaEvent(text))
                self._trim_assistant_output()
        elif kind == "interrupt" and self._status != "waiting_for_input":
            await self._set_status("waiting_for_input")

    async def _handle_custom_event(self, stream_item: dict[str, Any]) -> None:
        event_type = stream_item.get("event_type")
        if event_type in {"input.required", "sensitive_input.required"} and self._resuming:
            self._resuming = False
            return
        payload = stream_item.get("payload", {})
        if not isinstance(event_type, str) or not isinstance(payload, dict):
            return
        if event_type == "sensitive_input.required":
            deadline = self._start_sensitive_input_timeout()
            payload = {**payload, "expires_at": deadline}
        await self._emit(EventUtils.from_payload(event_type, payload))
        if event_type in {"input.required", "sensitive_input.required"}:
            await self._set_status("waiting_for_input")

    async def _withdraw_sensitive_input(self, reason: str) -> None:
        withdrawn = self._sensitive_inputs.withdraw(reason)
        if withdrawn:
            self._cancel_sensitive_input_timeout()
            await self._emit(SensitiveInputCancelledEvent(withdrawn.id, reason))

    async def _resume_without_code(self) -> None:
        reason = self._sensitive_inputs.take_withdrawn_reason()
        if reason:
            await self._await_task(self._execution_task)
            self._resuming = True
            await self._start_turn(self._runtime.resume_input({"entered": False, "reason": reason}))

    def _start_sensitive_input_timeout(self) -> datetime:
        self._cancel_sensitive_input_timeout()
        self._sensitive_input_timeout_task = asyncio.create_task(self._expire_sensitive_input())
        return datetime.now(UTC) + timedelta(seconds=self._settings.sensitive_input_window_seconds)

    def _cancel_sensitive_input_timeout(self) -> None:
        timeout_task = self._sensitive_input_timeout_task
        self._sensitive_input_timeout_task = None
        if timeout_task and timeout_task is not asyncio.current_task():
            timeout_task.cancel()

    async def _expire_sensitive_input(self) -> None:
        await asyncio.sleep(self._settings.sensitive_input_window_seconds)
        async with self._command_lock:
            if self._closed or self._status != "waiting_for_input" or not self._sensitive_inputs.pending:
                return
            self._sensitive_input_timeout_task = None
            await self._withdraw_sensitive_input(constants.SENSITIVE_INPUT_TIMEOUT_REASON)
            await self._resume_without_code()

    def _require_waiting(self) -> None:
        if self._closed or self._status != "waiting_for_input":
            raise SessionNotWaitingForInputError("The run is not waiting for user input")

    def _require_control_status(self) -> None:
        if self._closed or self._status not in {"waiting_for_input", "ready_for_follow_up"}:
            raise SessionNotWaitingForInputError("The browser is not available for user control")

    async def _release_control_mouse(self) -> None:
        if self._mouse_down:
            try:
                await self._browser.user_mouse_up()
            except Exception:
                logger.exception("Could not release the mouse")
            finally:
                self._mouse_down = False

    def _start_frame_capture(self) -> None:
        if self._capture_task is None or self._capture_task.done():
            self._capture_task = asyncio.create_task(self._capture_frames())

    async def _pause_frame_capture(self) -> None:
        capture_task = self._capture_task
        self._capture_task = None
        if capture_task:
            capture_task.cancel()
            await self._await_task(capture_task)

    async def _capture_frames(self) -> None:
        capture_failure_active = False
        failing_since = None
        while not self._closed:
            taken_over = self._controller_count > 0
            quality = FRAME_QUALITY_TAKEOVER if taken_over else FRAME_QUALITY
            try:
                frame = await asyncio.wait_for(
                    self._browser.capture_screen_frame(quality), timeout=constants.FRAME_CAPTURE_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exception:
                logger.warning("Browser frame capture failed: %s", exception)
                if failing_since is None:
                    failing_since = time.monotonic()
                outage_seconds = time.monotonic() - failing_since
                if not capture_failure_active and outage_seconds >= constants.FRAME_CAPTURE_GRACE_SECONDS:
                    capture_failure_active = True
                    await self._emit(BrowserCaptureFailedEvent("Live view paused; retrying automatically"))
                await asyncio.sleep(constants.FRAME_RETRY_INTERVAL_SECONDS)
                continue
            if frame is not None:
                async with self._frame_condition:
                    self._latest_frame = frame
                    self._frame_sequence += 1
                    self._frame_condition.notify_all()
                failing_since = None
                if capture_failure_active:
                    capture_failure_active = False
                    await self._emit(BrowserCaptureRecoveredEvent("Live view resumed"))
            await asyncio.sleep(
                constants.FRAME_INTERVAL_SECONDS_TAKEOVER if taken_over else constants.FRAME_INTERVAL_SECONDS,
            )

    async def _finish_resources(self) -> None:
        self._terminating = True
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._close_resources())
        await self._cleanup_task

    async def _close_resources(self) -> None:
        self._closed = True
        releasing_mouse = asyncio.create_task(self._release_control_mouse())
        await self._browser.close()
        await self._await_task(releasing_mouse)
        timeout_task = self._sensitive_input_timeout_task
        self._cancel_sensitive_input_timeout()
        await self._await_task(timeout_task)
        await self._pause_frame_capture()
        await self._event_queue.put(None)
        async with self._frame_condition:
            self._frame_condition.notify_all()

    async def _set_status(self, status: SessionStatus) -> None:
        if self._terminating or self._status == status:
            return
        async with self._control_lock:
            if self._terminating:
                return
            if status not in {"waiting_for_input", "ready_for_follow_up"}:
                await self._release_control_mouse()
                if self._terminating:
                    return
            await self._publish_status(status)

    async def _publish_status(self, status: SessionStatus) -> None:
        self._status = status
        await self._emit(SessionStatusEvent(status))

    async def _emit(self, event: AgentEvent) -> None:
        await self._event_queue.put(event)

    async def _await_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None or task is asyncio.current_task():
            return
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _trim_assistant_output(self) -> None:
        output_character_count = 0
        for part in self._assistant_output_parts:
            output_character_count += len(part)
        if output_character_count > constants.MAX_ASSISTANT_OUTPUT_CHARACTERS:
            combined_output = "".join(self._assistant_output_parts)
            self._assistant_output_parts = [combined_output[-constants.MAX_ASSISTANT_OUTPUT_CHARACTERS:]]


logger = logging.getLogger(__name__)
