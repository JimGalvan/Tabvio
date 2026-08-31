from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from tabvio.clock import utc_now


class User(BaseModel):
    """A Tabvio account, mapped one-to-one onto a WorkOS user."""

    id: UUID = Field(default_factory=uuid4)
    workos_user_id: str
    email: str
    created_at: datetime = Field(default_factory=utc_now)


class CurrentUserResponse(BaseModel):
    id: UUID
    email: str
