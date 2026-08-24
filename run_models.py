from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
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
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RunEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    sequence: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class CreateRunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=10_000)
    max_runtime_seconds: int = Field(default=600, ge=30, le=1_800)


class UserInputRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=10_000)


class RunResponse(BaseModel):
    run: RunRecord
    stream_url: str
    screen_url: str

