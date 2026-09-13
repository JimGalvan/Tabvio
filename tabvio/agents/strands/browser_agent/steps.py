from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from tabvio.browser.session import BrowserSession
from tabvio.credentials.models import CredentialField


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
    field: CredentialField
    element_index: int = Field(ge=0)


class MfaCodeStep(StrictStep):
    action: Literal["request_mfa_code"]
    element_index: int = Field(ge=0)
    prompt: str = Field(min_length=1, max_length=240)
    submit_element_index: int | None = Field(default=None, ge=0)


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


def require_submit_element(
        browser: BrowserSession, submit_element_index: int | None
) -> None:
    """The control that sends the code, when the plan names one."""
    if submit_element_index is None:
        return
    if browser.get_stored_element(submit_element_index) is None:
        raise ValueError(
            f"element [{submit_element_index}] is not in the latest observation"
        )


def validate_plan(browser: BrowserSession, steps: list[BrowserStep]) -> None:
    all_steps_except_last = steps[:-1]
    for step in all_steps_except_last:
        if isinstance(step, (ClickStep, PressStep, MfaCodeStep)):
            raise ValueError(
                f"{step.action} must be final; observe before planning more actions"
            )

    for step in steps:
        if isinstance(step, CredentialFillStep):
            require_fillable_element(
                browser, step.element_index, password=step.field == "password"
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
            require_submit_element(browser, step.submit_element_index)


def element_label(browser: BrowserSession, element_index: int) -> str:
    element = browser.get_stored_element(element_index)
    if element is None:
        return f"Element {element_index}"
    text = " ".join(element.text.split())
    return text[:80] if text else f"{element.tag} element {element_index}"


def step_reference(step: BrowserStep) -> str:
    if isinstance(step, CredentialFillStep):
        return f"fill_credential.{step.field}[{step.element_index}]"
    return f"{step.action}[{step.element_index}]"


def step_event_payload(browser: BrowserSession, step: BrowserStep) -> dict[str, Any]:
    return {"action": step.action, "target": element_label(browser, step.element_index)}
