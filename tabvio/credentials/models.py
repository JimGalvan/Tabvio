from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, SecretStr, field_validator

from tabvio.clock import utc_now


class CredentialRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    name: str
    allowed_domains: list[str]
    login_hint: str
    encrypted_payload: bytes
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    revoked_at: datetime | None = None


class CredentialMetadata(BaseModel):
    id: UUID
    name: str
    allowed_domains: list[str]
    login_hint: str
    created_at: datetime
    updated_at: datetime


class CreateCredentialRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    login: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=2_000)
    allowed_domains: list[str] = Field(min_length=1, max_length=20)

    @field_validator("name", "login")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class UpdateCredentialRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    login: str | None = Field(default=None, min_length=1, max_length=320)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=2_000)
    allowed_domains: list[str] | None = Field(default=None, min_length=1, max_length=20)

    @field_validator("name", "login")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class CredentialListResponse(BaseModel):
    credentials: list[CredentialMetadata]


class CredentialSecret(BaseModel):
    login: str
    password: str
