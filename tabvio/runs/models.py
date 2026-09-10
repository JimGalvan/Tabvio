from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, SecretStr, model_validator

from tabvio.browser.constants import (
    CONTROL_KEYS,
    MAX_CONTROL_SCROLL_PIXELS,
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTH,
)
from tabvio.clock import utc_now

if TYPE_CHECKING:
    from tabvio.runs.runtime import AgentRuntime


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    READY_FOR_FOLLOW_UP = "ready_for_follow_up"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        return self in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }


class RunRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID = Field(default_factory=uuid4)
    user_id: UUID | None = None
    credential_ids: list[UUID] = Field(default_factory=list, max_length=25)
    task: str
    status: RunStatus = RunStatus.QUEUED
    max_runtime_seconds: int
    final_output: str | None = None
    error: str | None = None
    follow_up_expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RunEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    sequence: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


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
    follow_up_expiry_task: asyncio.Task[None] | None = None
    sensitive_input_timeout_task: asyncio.Task[None] | None = None
    assistant_output_parts: list[str] = field(default_factory=list)
    controller_count: int = 0
    mouse_controller: str | None = None
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CreateRunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=10_000)
    max_runtime_seconds: int = Field(default=600, ge=30, le=1_800)
    credential_ids: list[UUID] = Field(default_factory=list, max_length=25)


class UserInputRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=10_000)


class SensitiveInputRequest(BaseModel):
    request_id: UUID
    code: SecretStr = Field(min_length=1, max_length=128)


class FollowUpRequest(BaseModel):
    task: str = Field(min_length=1, max_length=10_000)


class BrowserControlEvent(BaseModel):
    type: Literal["click", "mouse_down", "mouse_up", "scroll", "key", "text"]
    x: float = Field(default=0.0, ge=0, le=VIEWPORT_WIDTH)
    y: float = Field(default=0.0, ge=0, le=VIEWPORT_HEIGHT)
    delta_y: float = Field(
        default=0.0,
        ge=-MAX_CONTROL_SCROLL_PIXELS,
        le=MAX_CONTROL_SCROLL_PIXELS,
    )
    key: str = Field(default="", max_length=16)
    text: str = Field(default="", max_length=1_000)

    @model_validator(mode="after")
    def check_payload_matches_type(self) -> BrowserControlEvent:
        if self.type == "key" and self.key not in CONTROL_KEYS:
            raise ValueError(f"{self.key!r} is not a forwardable key")

        if self.type == "text" and not self.text:
            raise ValueError("A text event carries no text")

        return self


class RunSummary(BaseModel):
    """A run as it appears in the dashboard history list."""

    id: UUID
    task: str
    status: RunStatus
    created_at: datetime
    updated_at: datetime


class RunListResponse(BaseModel):
    runs: list[RunSummary]


class RunResponse(BaseModel):
    run: RunRecord
    stream_url: str
    screen_url: str
    control_url: str
