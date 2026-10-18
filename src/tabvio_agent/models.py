from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, TypeAlias
from uuid import UUID

CredentialField: TypeAlias = Literal["login", "password", "email", "first_name", "last_name", "phone"]
BrowserAction: TypeAlias = Literal["click", "fill", "select", "press", "fill_credential", "request_mfa_code"]
SessionStatus: TypeAlias = Literal["running", "waiting_for_input", "ready_for_follow_up", "failed", "cancelled", "timed_out"]
ControlKey: TypeAlias = Literal[
    "Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowUp", "ArrowDown",
    "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", "Control+A", "Meta+A",
]


class VerificationMethod(StrEnum):
    SMS = "sms"
    EMAIL = "email"
    AUTHENTICATOR_APP = "authenticator_app"
    PHONE_CALL = "phone_call"
    PUSH = "push"
    ASK = "ask"


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


@dataclass(frozen=True)
class Todo:
    content: str
    status: Literal["pending", "in_progress", "completed"]


@dataclass(frozen=True)
class ClickAction:
    x: float
    y: float
    type: Literal["click"] = field(default="click", init=False)


@dataclass(frozen=True)
class MouseDownAction:
    x: float
    y: float
    type: Literal["mouse_down"] = field(default="mouse_down", init=False)


@dataclass(frozen=True)
class MouseUpAction:
    type: Literal["mouse_up"] = field(default="mouse_up", init=False)


@dataclass(frozen=True)
class ScrollAction:
    x: float
    y: float
    delta_y: float
    type: Literal["scroll"] = field(default="scroll", init=False)


@dataclass(frozen=True)
class KeyAction:
    key: ControlKey
    type: Literal["key"] = field(default="key", init=False)


@dataclass(frozen=True)
class TextAction:
    text: str
    type: Literal["text"] = field(default="text", init=False)


BrowserControlAction: TypeAlias = ClickAction | MouseDownAction | MouseUpAction | ScrollAction | KeyAction | TextAction
