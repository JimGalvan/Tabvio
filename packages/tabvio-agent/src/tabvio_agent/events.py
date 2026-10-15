from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, TypeAlias
from uuid import UUID

from tabvio_agent.models import BrowserAction, Todo


@dataclass(frozen=True)
class AgentMessageDeltaEvent:
    text: str
    event_type: Literal["agent.message.delta"] = field(default="agent.message.delta", init=False)


@dataclass(frozen=True)
class InputRequiredEvent:
    question: str
    payment_signals: tuple[str, ...] | None = None
    event_type: Literal["input.required"] = field(default="input.required", init=False)


@dataclass(frozen=True)
class InputReceivedEvent:
    event_type: Literal["input.received"] = field(default="input.received", init=False)


@dataclass(frozen=True)
class SensitiveInputRequiredEvent:
    request_id: UUID
    kind: Literal["mfa_code"]
    prompt: str
    expires_at: datetime
    event_type: Literal["sensitive_input.required"] = field(default="sensitive_input.required", init=False)


@dataclass(frozen=True)
class SensitiveInputReceivedEvent:
    request_id: UUID
    kind: Literal["mfa_code"]
    event_type: Literal["sensitive_input.received"] = field(default="sensitive_input.received", init=False)


@dataclass(frozen=True)
class SensitiveInputCancelledEvent:
    request_id: UUID
    reason: str
    event_type: Literal["sensitive_input.cancelled"] = field(default="sensitive_input.cancelled", init=False)


@dataclass(frozen=True)
class BrowserNavigationStartedEvent:
    url: str
    event_type: Literal["browser.navigation.started"] = field(default="browser.navigation.started", init=False)


@dataclass(frozen=True)
class BrowserNavigationCompletedEvent:
    url: str
    event_type: Literal["browser.navigation.completed"] = field(default="browser.navigation.completed", init=False)


@dataclass(frozen=True)
class BrowserActionStartedEvent:
    action: BrowserAction
    target: str
    event_type: Literal["browser.action.started"] = field(default="browser.action.started", init=False)


@dataclass(frozen=True)
class BrowserActionCompletedEvent:
    action: BrowserAction
    target: str
    event_type: Literal["browser.action.completed"] = field(default="browser.action.completed", init=False)


@dataclass(frozen=True)
class BrowserActionFailedEvent:
    action: BrowserAction
    target: str
    error: str
    event_type: Literal["browser.action.failed"] = field(default="browser.action.failed", init=False)


@dataclass(frozen=True)
class BrowserTabChangedEvent:
    tab_id: str
    event_type: Literal["browser.tab.changed"] = field(default="browser.tab.changed", init=False)


@dataclass(frozen=True)
class BrowserObservationEvent:
    message: str
    event_type: Literal["browser.observation"] = field(default="browser.observation", init=False)


@dataclass(frozen=True)
class TaskTodosEvent:
    todos: tuple[Todo, ...]
    event_type: Literal["task.todos"] = field(default="task.todos", init=False)


@dataclass(frozen=True)
class RunCompletedEvent:
    output: str
    event_type: Literal["run.completed"] = field(default="run.completed", init=False)


@dataclass(frozen=True)
class RunFailedEvent:
    error: str
    event_type: Literal["run.failed"] = field(default="run.failed", init=False)


@dataclass(frozen=True)
class RunCancelledEvent:
    event_type: Literal["run.cancelled"] = field(default="run.cancelled", init=False)


@dataclass(frozen=True)
class TakeoverStartedEvent:
    event_type: Literal["takeover.started"] = field(default="takeover.started", init=False)


@dataclass(frozen=True)
class TakeoverEndedEvent:
    event_type: Literal["takeover.ended"] = field(default="takeover.ended", init=False)


@dataclass(frozen=True)
class BrowserCaptureFailedEvent:
    message: str
    event_type: Literal["browser.capture.failed"] = field(default="browser.capture.failed", init=False)


@dataclass(frozen=True)
class BrowserCaptureRecoveredEvent:
    message: str
    event_type: Literal["browser.capture.recovered"] = field(default="browser.capture.recovered", init=False)


AgentEvent: TypeAlias = (
    AgentMessageDeltaEvent
    | InputRequiredEvent
    | InputReceivedEvent
    | SensitiveInputRequiredEvent
    | SensitiveInputReceivedEvent
    | SensitiveInputCancelledEvent
    | BrowserNavigationStartedEvent
    | BrowserNavigationCompletedEvent
    | BrowserActionStartedEvent
    | BrowserActionCompletedEvent
    | BrowserActionFailedEvent
    | BrowserTabChangedEvent
    | BrowserObservationEvent
    | TaskTodosEvent
    | RunCompletedEvent
    | RunFailedEvent
    | RunCancelledEvent
    | TakeoverStartedEvent
    | TakeoverEndedEvent
    | BrowserCaptureFailedEvent
    | BrowserCaptureRecoveredEvent
)
