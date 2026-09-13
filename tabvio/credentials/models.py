from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, SecretStr, field_validator

from tabvio.clock import utc_now

MAX_VERIFICATION_PREFERENCES = 5
CredentialField = Literal["login", "password", "email", "first_name", "last_name", "phone"]


class VerificationMethod(StrEnum):
    SMS = "sms"
    EMAIL = "email"
    AUTHENTICATOR_APP = "authenticator_app"
    PHONE_CALL = "phone_call"
    PUSH = "push"
    ASK = "ask"


class CredentialRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    name: str
    allowed_domains: list[str]
    login_hint: str
    encrypted_payload: bytes
    available_fields: list[CredentialField] = Field(default_factory=lambda: ["login", "password"])
    is_default: bool = False
    preferred_verification: list[VerificationMethod] = Field(
        default_factory=list, max_length=MAX_VERIFICATION_PREFERENCES
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    revoked_at: datetime | None = None


class CredentialMetadata(BaseModel):
    id: UUID
    name: str
    allowed_domains: list[str]
    login_hint: str
    available_fields: list[CredentialField] = Field(default_factory=lambda: ["login", "password"])
    is_default: bool
    preferred_verification: list[VerificationMethod] = Field(
        default_factory=list, max_length=MAX_VERIFICATION_PREFERENCES
    )
    created_at: datetime
    updated_at: datetime


class CredentialInputFields(BaseModel):
    login: str | None = Field(default=None, max_length=320)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=2_000)
    email: str | None = Field(default=None, max_length=320)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=50)

    @field_validator("login", "email", "first_name", "last_name", "phone")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class CreateCredentialRequest(CredentialInputFields):
    name: str = Field(min_length=1, max_length=80)
    allowed_domains: list[str] = Field(min_length=1, max_length=20)
    is_default: bool = False
    preferred_verification: list[VerificationMethod] = Field(
        default_factory=list, max_length=MAX_VERIFICATION_PREFERENCES
    )

    @field_validator("name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class UpdateCredentialRequest(CredentialInputFields):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    allowed_domains: list[str] | None = Field(default=None, min_length=1, max_length=20)
    is_default: bool | None = None
    preferred_verification: list[VerificationMethod] | None = Field(
        default=None, max_length=MAX_VERIFICATION_PREFERENCES
    )

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class CredentialListResponse(BaseModel):
    credentials: list[CredentialMetadata]


class CredentialSecret(BaseModel):
    login: str | None = None
    password: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
