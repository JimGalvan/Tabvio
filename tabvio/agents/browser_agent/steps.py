from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from tabvio.browser.session import BrowserSession


class StrictStep(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClickStep(StrictStep):
    action: Literal["click"]
    element_index: int = Field(ge=0)


class FillStep(StrictStep):
    action: Literal["fill"]
    element_index: int = Field(ge=0)
    value: str


class SelectStep(StrictStep):
    action: Literal["select"]
    element_index: int = Field(ge=0)
    value: str


class PressStep(StrictStep):
    action: Literal["press"]
    element_index: int = Field(ge=0)
    value: str


class CredentialFillStep(StrictStep):
    action: Literal["fill_credential"]
    credential_id: UUID
    username_element_index: int = Field(ge=0)
    password_element_index: int = Field(ge=0)


class MfaCodeStep(StrictStep):
    action: Literal["request_mfa_code"]
    element_index: int = Field(ge=0)
    prompt: str = Field(min_length=1, max_length=240)


BrowserStep = Annotated[
    ClickStep | FillStep | SelectStep | PressStep | CredentialFillStep | MfaCodeStep,
    Field(discriminator="action"),
]
BROWSER_STEP_ADAPTER = TypeAdapter(BrowserStep)


class StepPlan(BaseModel):
    steps: list[BrowserStep] = Field(min_length=1, max_length=10)


def require_fillable_element(
        browser: BrowserSession, element_index: int, *, password: bool = False
) -> None:
    element = browser.get_stored_element(element_index)
    if element is None:
        raise ValueError(f"element [{element_index}] is not in the latest observation")
    if element.tag.lower() not in {"input", "textarea"}:
        raise ValueError(
            f"fill[{element_index}] targets <{element.tag}>, not an input or textarea"
        )
    if password and "type=password" not in element.attrs.lower():
        raise ValueError(
            f"password field [{element_index}] is not an input with type=password"
        )


def validate_plan(browser: BrowserSession, steps: list[BrowserStep]) -> None:
    for step in steps[:-1]:
        if isinstance(step, (ClickStep, SelectStep, PressStep, MfaCodeStep)):
            raise ValueError(
                f"{step.action} must be final; observe before planning more actions"
            )

    for step in steps:
        if isinstance(step, CredentialFillStep):
            require_fillable_element(browser, step.username_element_index)
            require_fillable_element(
                browser, step.password_element_index, password=True
            )
            continue

        element = browser.get_stored_element(step.element_index)
        if element is None:
            raise ValueError(
                f"element [{step.element_index}] is not in the latest observation"
            )
        if isinstance(step, FillStep):
            require_fillable_element(browser, step.element_index)
        if isinstance(step, MfaCodeStep):
            require_fillable_element(browser, step.element_index)


def element_label(browser: BrowserSession, element_index: int) -> str:
    element = browser.get_stored_element(element_index)
    if element is None:
        return f"Element {element_index}"
    text = " ".join(element.text.split())
    return text[:80] if text else f"{element.tag} element {element_index}"


def step_reference(step: BrowserStep) -> str:
    if isinstance(step, CredentialFillStep):
        return (
            f"fill_credential[{step.username_element_index},"
            f"{step.password_element_index}]"
        )
    return f"{step.action}[{step.element_index}]"


def step_event_payload(browser: BrowserSession, step: BrowserStep) -> dict[str, Any]:
    if isinstance(step, CredentialFillStep):
        target = (
            f"{element_label(browser, step.username_element_index)} and "
            f"{element_label(browser, step.password_element_index)}"
        )
    else:
        target = element_label(browser, step.element_index)
    return {"action": step.action, "target": target}
