from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from tabvio.agent.runtime import AgentRuntime


def utc_now() -> datetime:
    return datetime.now(UTC)


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
    assistant_output_parts: list[str] = field(default_factory=list)


class CreateRunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=10_000)
    max_runtime_seconds: int = Field(default=600, ge=30, le=1_800)


class UserInputRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=10_000)


class FollowUpRequest(BaseModel):
    task: str = Field(min_length=1, max_length=10_000)


class RunResponse(BaseModel):
    run: RunRecord
    stream_url: str
    screen_url: str
