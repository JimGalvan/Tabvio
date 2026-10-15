from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

CredentialField = Literal["login", "password", "email", "first_name", "last_name", "phone"]


@dataclass(frozen=True)
class CredentialMetadata:
    id: UUID
    name: str
    allowed_domains: tuple[str, ...]
    login_hint: str
    is_default: bool
    available_fields: tuple[CredentialField, ...] = ("login", "password")
    preferred_verification: tuple[str, ...] = ()


@dataclass(frozen=True)
class CredentialSecret:
    login: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    email: str | None = field(default=None, repr=False)
    first_name: str | None = field(default=None, repr=False)
    last_name: str | None = field(default=None, repr=False)
    phone: str | None = field(default=None, repr=False)
