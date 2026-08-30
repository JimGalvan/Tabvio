import json
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from langgraph.config import get_stream_writer
from langgraph.types import interrupt
from pydantic import BaseModel, Field, model_validator

from browser_session import BrowserSession


class BrowserStep(BaseModel):
    action: Literal["click", "fill", "select", "press"]
    element_index: int = Field(ge=0)
    value: str | None = None

    @model_validator(mode="after")
    def require_value(self):
        if self.action != "click" and self.value is None:
            raise ValueError(f"{self.action} requires a value")

        return self


class StepPlan(BaseModel):
    steps: list[BrowserStep] = Field(min_length=1, max_length=10)


def _publish_custom_event(event_type: str, payload: dict[str, Any]) -> None:
    writer = get_stream_writer()
    writer({"event_type": event_type, "payload": payload})


def _validate_plan(browser: BrowserSession, steps: list[BrowserStep]) -> None:
    for step in steps[:-1]:
        if step.action in {"click", "select", "press"}:
            raise ValueError(f"{step.action}[{step.element_index}] must be final; observe before planning more actions")

    for step in steps:
        element = browser.get_stored_element(step.element_index)
        if element is None:
            raise ValueError(f"element [{step.element_index}] is not in the latest observation")

        if step.action == "fill" and element.tag.lower() not in {"input", "textarea"}:
            raise ValueError(f"fill[{step.element_index}] targets <{element.tag}>, not an input or textarea")


def _element_label(browser: BrowserSession, element_index: int) -> str:
    element = browser.get_stored_element(element_index)
    if element is None:
        return f"Element {element_index}"

    text = " ".join(element.text.split())
    if text:
        return text[:80]

    return f"{element.tag} element {element_index}"


def build_browser_tools(browser: BrowserSession) -> list[BaseTool]:
    """Build the browser tools for the main agent, bound to one session."""

    @tool
    async def navigate_and_observe(url: str) -> str:
        """Navigate to a URL and return the resulting page snapshot."""
        _publish_custom_event("browser.navigation.started", {"url": url})
        observation = await browser.navigate_and_observe(url)
        _publish_custom_event("browser.navigation.completed", {"url": url})
        return observation

    @tool
    async def observe_page() -> str:
        """Return the current page snapshot without navigating."""
        observation = await browser.observe_page()
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
        """Pause and ask the user for required information."""
        _publish_custom_event("input.required", {"question": question})
        answer = interrupt({"kind": "question", "question": question})
        return str(answer)

    @tool(args_schema=StepPlan)
    async def execute_steps(steps: list[BrowserStep]) -> str:
        """Validate and execute browser steps sequentially without another LLM."""
        normalized_steps = []
        for step in steps:
            if isinstance(step, BrowserStep):
                normalized_steps.append(step)
            else:
                normalized_steps.append(BrowserStep.model_validate(step))

        try:
            _validate_plan(browser, normalized_steps)
        except ValueError as exception:
            return json.dumps({
                    "ok": False,
                    "kind": "validation_error",
                    "completed": [],
                    "failed": None,
                    "error": str(exception),
                })

        completed: list[str] = []
        for step in normalized_steps:
            step_reference = f"{step.action}[{step.element_index}]"
            event_payload = {
                "action": step.action,
                "element_index": step.element_index,
                "target": _element_label(browser, step.element_index),
            }
            _publish_custom_event("browser.action.started", event_payload)

            try:
                if step.action == "click":
                    await browser.click(step.element_index)
                elif step.action == "fill" and step.value is not None:
                    await browser.fill(step.element_index, step.value)
                elif step.action == "select" and step.value is not None:
                    await browser.select(step.element_index, step.value)
                elif step.action == "press" and step.value is not None:
                    await browser.press(step.element_index, step.value)

                completed.append(step_reference)
                _publish_custom_event("browser.action.completed", event_payload)
            except Exception as exception:
                _publish_custom_event("browser.action.failed",
                                      {**event_payload, "error": str(exception)},)
                return json.dumps({
                        "ok": False,
                        "kind": "execution_error",
                        "completed": completed,
                        "failed": step_reference,
                        "error": str(exception),
                    })

        return json.dumps(
            {"ok": True, "kind": "success", "completed": completed, "failed": None}
        )

    execute_steps.handle_validation_error = True

    return [
        navigate_and_observe,
        observe_page,
        execute_steps,
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
