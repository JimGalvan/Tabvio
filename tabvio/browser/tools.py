import asyncio
import json
import time
from typing import Annotated, Any, Literal
from uuid import UUID

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool
from langgraph.config import get_stream_writer
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from tabvio.agent.context import AgentContext
from tabvio.agent.sensitive_input import SensitiveInputChannel
from tabvio.agent.page_load_detector import build_page_loader_detector_subagent
from tabvio.browser.session import BrowserSession
from tabvio.credentials.service import CredentialService


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
_BROWSER_STEP_ADAPTER = TypeAdapter(BrowserStep)


class StepPlan(BaseModel):
    steps: list[BrowserStep] = Field(min_length=1, max_length=10)


def _publish_custom_event(event_type: str, payload: dict[str, Any]) -> None:
    writer = get_stream_writer()
    writer({"event_type": event_type, "payload": payload})


def _require_fillable_element(
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


def _validate_plan(browser: BrowserSession, steps: list[BrowserStep]) -> None:
    for step in steps[:-1]:
        if isinstance(step, (ClickStep, SelectStep, PressStep, MfaCodeStep)):
            raise ValueError(
                f"{step.action} must be final; observe before planning more actions"
            )

    for step in steps:
        if isinstance(step, CredentialFillStep):
            _require_fillable_element(browser, step.username_element_index)
            _require_fillable_element(
                browser, step.password_element_index, password=True
            )
            continue

        element = browser.get_stored_element(step.element_index)
        if element is None:
            raise ValueError(
                f"element [{step.element_index}] is not in the latest observation"
            )
        if isinstance(step, FillStep):
            _require_fillable_element(browser, step.element_index)
        if isinstance(step, MfaCodeStep):
            _require_fillable_element(browser, step.element_index)


def _element_label(browser: BrowserSession, element_index: int) -> str:
    element = browser.get_stored_element(element_index)
    if element is None:
        return f"Element {element_index}"
    text = " ".join(element.text.split())
    return text[:80] if text else f"{element.tag} element {element_index}"


def _step_reference(step: BrowserStep) -> str:
    if isinstance(step, CredentialFillStep):
        return (
            f"fill_credential[{step.username_element_index},"
            f"{step.password_element_index}]"
        )
    return f"{step.action}[{step.element_index}]"


def _step_event_payload(browser: BrowserSession, step: BrowserStep) -> dict[str, Any]:
    if isinstance(step, CredentialFillStep):
        target = (
            f"{_element_label(browser, step.username_element_index)} and "
            f"{_element_label(browser, step.password_element_index)}"
        )
    else:
        target = _element_label(browser, step.element_index)
    return {"action": step.action, "target": target}


def build_browser_tools(
        browser: BrowserSession,
        credential_service: CredentialService | None = None,
        sensitive_inputs: SensitiveInputChannel | None = None,
) -> list[BaseTool]:
    """Build the tools for one browser run and its security boundaries."""
    sensitive_inputs = sensitive_inputs or SensitiveInputChannel()

    page_load_detector_subagent = build_page_loader_detector_subagent()

    @tool
    async def get_text_in_viewport() -> str:
        """Return text visible in the current browser viewport."""
        return await browser.get_text_in_viewport()

    @tool
    async def navigate_and_observe(url: str) -> str:
        """Navigate to a URL and return the resulting page snapshot."""
        _publish_custom_event("browser.navigation.started", {"url": url})

        delays = [1, 2, 3, 5]
        is_page_loaded = False
        observation = ""
        for delay in delays:
            if is_page_loaded:
                break

            time.sleep(delay)
            if delay == 1:
                observation = await browser.attempt_navigate_and_observe(url)
            else:
                observation = await browser.attempt_observe_page()
            USER_PROMPT = f"""Determine whether this page is loaded.
                        Page snapshot:
                        {observation}
                        """
            result = await page_load_detector_subagent.ainvoke({"messages": [{"role": "user", "content": USER_PROMPT}]})
            is_page_loaded = "true" in result["messages"][-1].content

        _publish_custom_event("browser.navigation.completed", {"url": url})
        return observation

    @tool
    async def observe_page() -> str:
        """Return the current page snapshot without navigating."""
        observation = await browser.attempt_observe_page()
        _publish_custom_event(
            "browser.observation", {"message": "Observed the current page"}
        )
        return observation

    @tool
    async def switch_tab(tab_id: str) -> str:
        """Switch to the tab identified by a value such as `tab:1`."""
        result = await browser.switch_tab(tab_id)
        _publish_custom_event("browser.tab.changed", {"tab_id": tab_id})
        return result

    @tool
    def request_user_input(question: str) -> str:
        """Pause and ask the user for required non-sensitive information."""
        _publish_custom_event("input.required", {"question": question})
        answer = interrupt({"kind": "question", "question": question})
        return str(answer)

    @tool
    async def list_selected_credentials(
            runtime: ToolRuntime[AgentContext],
    ) -> str:
        """List safe metadata for credentials selected for this run."""
        if not runtime.context.credential_ids:
            return "[]"
        if credential_service is None or runtime.context.user_id is None:
            raise RuntimeError("Credential storage is not configured")
        metadata = await asyncio.to_thread(
            credential_service.require_selected,
            runtime.context.credential_ids,
            runtime.context.user_id,
        )
        return json.dumps(
            [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "allowed_domains": item.allowed_domains,
                }
                for item in metadata
            ]
        )

    @tool(args_schema=StepPlan)
    async def execute_steps(
            steps: list[BrowserStep], runtime: ToolRuntime[AgentContext]
    ) -> str:
        """Validate and execute browser steps, including credential and MFA steps."""
        normalized_steps = [
            step
            if isinstance(step, BaseModel)
            else _BROWSER_STEP_ADAPTER.validate_python(step)
            for step in steps
        ]
        try:
            _validate_plan(browser, normalized_steps)
        except ValueError as exception:
            return json.dumps(
                {
                    "ok": False,
                    "kind": "validation_error",
                    "completed": [],
                    "failed": None,
                    "error": str(exception),
                }
            )

        completed: list[str] = []
        for step in normalized_steps:
            step_reference = _step_reference(step)
            event_payload = _step_event_payload(browser, step)
            _publish_custom_event("browser.action.started", event_payload)
            try:
                if isinstance(step, ClickStep):
                    await browser.click(step.element_index)
                elif isinstance(step, FillStep):
                    await browser.fill(step.element_index, step.value)
                elif isinstance(step, SelectStep):
                    await browser.select(step.element_index, step.value)
                elif isinstance(step, PressStep):
                    await browser.press(step.element_index, step.value)
                elif isinstance(step, CredentialFillStep):
                    if credential_service is None or runtime.context.user_id is None:
                        raise RuntimeError("Credential storage is not configured")
                    if step.credential_id not in runtime.context.credential_ids:
                        raise ValueError("Credential is not selected for this run")
                    secret = await asyncio.to_thread(
                        credential_service.resolve_for_domain,
                        step.credential_id,
                        runtime.context.user_id,
                        browser.current_hostname,
                    )
                    await browser.fill(step.username_element_index, secret.login)
                    await browser.fill(step.password_element_index, secret.password)
                    del secret
                elif isinstance(step, MfaCodeStep):
                    request = sensitive_inputs.begin(step.element_index, step.prompt)
                    _publish_custom_event(
                        "sensitive_input.required",
                        {
                            "request_id": str(request.id),
                            "kind": request.kind,
                            "prompt": request.prompt,
                        },
                    )
                    result = interrupt(
                        {"kind": request.kind, "request_id": str(request.id)}
                    )
                    if not isinstance(result, dict) or result.get("entered") is not True:
                        raise RuntimeError("The verification code was not entered")
                    sensitive_inputs.clear(request.id)

                completed.append(step_reference)
                _publish_custom_event("browser.action.completed", event_payload)
            except Exception as exception:
                _publish_custom_event(
                    "browser.action.failed", {**event_payload, "error": str(exception)}
                )
                return json.dumps(
                    {
                        "ok": False,
                        "kind": "execution_error",
                        "completed": completed,
                        "failed": step_reference,
                        "error": str(exception),
                    }
                )

        return json.dumps(
            {"ok": True, "kind": "success", "completed": completed, "failed": None}
        )

    execute_steps.handle_validation_error = True

    return [
        navigate_and_observe,
        observe_page,
        execute_steps,
        list_selected_credentials,
        switch_tab,
        request_user_input,
    ]


def build_page_navigator_tools(browser: BrowserSession) -> list[BaseTool]:
    """Build the viewport tools for the page-navigator subagent."""

    @tool
    async def get_text_in_viewport() -> str:
        """Return text visible in the current browser viewport."""
        return await browser.get_text_in_viewport()

    @tool
    async def scroll(amount: float) -> str:
        """Scroll by a multiple of the viewport height."""
        return await browser.scroll(amount)

    return [get_text_in_viewport, scroll]
