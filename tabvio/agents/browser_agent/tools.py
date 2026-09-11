import asyncio
import json
import time

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool
from langgraph.types import interrupt
from pydantic import BaseModel

from tabvio.agents.browser_agent.context import AgentContext
from tabvio.agents.shared.events import publish_custom_event
from tabvio.agents.shared.verification import describe_entered_code, explain_missing_code
from tabvio.runs.sensitive_input import SensitiveInputChannel
from tabvio.agents.shared.utils import Utils
from tabvio.agents.browser_agent.steps import (
    BROWSER_STEP_ADAPTER,
    BrowserStep,
    ClickStep,
    CredentialFillStep,
    FillStep,
    MfaCodeStep,
    PressStep,
    SelectStep,
    StepPlan,
    step_event_payload,
    step_reference,
    validate_plan,
)
from tabvio.agents.page_load_detector.page_load_detector import build_page_loader_detector_subagent
from tabvio.browser.session import BrowserSession
from tabvio.credentials.service import CredentialService


def build_browser_tools(
        browser: BrowserSession,
        credential_service: CredentialService | None = None,
        sensitive_inputs: SensitiveInputChannel | None = None,
) -> list[BaseTool]:
    """Build the tools for one browser run and its security boundaries."""
    sensitive_inputs = sensitive_inputs or SensitiveInputChannel()
    acknowledged_payment_surface: str | None = None
    pending_payment_observation = None

    page_load_detector_subagent = build_page_loader_detector_subagent()

    @tool
    async def get_text_in_viewport() -> str:
        """Return text visible in the current browser viewport."""
        return await browser.get_text_in_viewport()

    @tool
    async def navigate_and_observe(url: str) -> str:
        """Navigate to a URL and return the resulting page snapshot."""
        if pending_payment_observation is not None:
            return await finish_observation("")
        publish_custom_event("browser.navigation.started", {"url": url})
        observation = await browser.attempt_navigate_and_observe(url)
        publish_custom_event("browser.navigation.completed", {"url": url})
        return await finish_observation(observation.page_state)

    @tool
    async def observe_page() -> str:
        """Return the current page snapshot without navigating."""
        if pending_payment_observation is not None:
            return await finish_observation("")
        observation = await browser.attempt_observe_page()
        publish_custom_event(
            "browser.observation", {"message": "Observed the current page"}
        )
        return await finish_observation(observation.page_state)

    @tool
    async def switch_tab(tab_id: str) -> str:
        """Switch to the tab identified by a value such as `tab:1`."""
        if pending_payment_observation is not None:
            return await finish_observation("")
        result = await browser.switch_tab(tab_id)
        publish_custom_event("browser.tab.changed", {"tab_id": tab_id})
        return await finish_observation(result)

    @tool
    def request_user_input(question: str) -> str:
        """Pause and ask the user for required non-sensitive information."""
        publish_custom_event("input.required", {"question": question})
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
                    "preferred_verification": [
                        method.value for method in item.preferred_verification
                    ],
                }
                for item in metadata
            ]
        )

    async def finish_observation(page_state: str) -> str:
        nonlocal pending_payment_observation

        detection = pending_payment_observation or browser.payment_detection_result
        if not detection.needs_handoff(acknowledged_payment_surface):
            return page_state

        pending_payment_observation = detection
        guard_payment_surface(detection)
        pending_payment_observation = None
        observation = await browser.attempt_observe_page()
        return observation.page_state

    def guard_payment_surface(detection=None) -> None:
        nonlocal acknowledged_payment_surface

        detection = detection or browser.payment_detection_result
        if not detection.needs_handoff(acknowledged_payment_surface):
            return

        question = (
            "This page can take a payment, so I have stopped before touching it. "
            "Take control of the browser, enter the payment details yourself, "
            "then tell me when I can continue."
        )
        payload = {
            "question": question,
            "payment_signals": detection.get_signals,
        }
        publish_custom_event("input.required", payload)

        interrupt({"kind": "payment_handoff", "question": question})
        acknowledged_payment_surface = detection.fingerprint

    @tool(args_schema=StepPlan)
    async def execute_steps(
            steps: list[BrowserStep], runtime: ToolRuntime[AgentContext]
    ) -> str:
        """Validate and execute browser steps, including credential and MFA steps."""
        guard_payment_surface()
        normalized_steps = [
            step
            if isinstance(step, BaseModel)
            else BROWSER_STEP_ADAPTER.validate_python(step)
            for step in steps
        ]
        try:
            validate_plan(browser, normalized_steps)
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
        notes: list[str] = []
        for step in normalized_steps:
            reference = step_reference(step)
            event_payload = step_event_payload(browser, step)
            publish_custom_event("browser.action.started", event_payload)
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
                    value = secret.login if step.field == "login" else secret.password
                    await browser.fill(step.element_index, value)
                    del secret, value
                elif isinstance(step, MfaCodeStep):
                    request = sensitive_inputs.begin(
                        step.element_index, step.prompt, step.submit_element_index
                    )
                    publish_custom_event(
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
                    sensitive_inputs.clear(request.id)
                    if not isinstance(result, dict) or result.get("entered") is not True:
                        raise RuntimeError(explain_missing_code(result))
                    notes.append(describe_entered_code(result))

                completed.append(reference)
                publish_custom_event("browser.action.completed", event_payload)
            except Exception as exception:
                publish_custom_event(
                    "browser.action.failed", {**event_payload, "error": str(exception)}
                )
                return json.dumps(
                    {
                        "ok": False,
                        "kind": "execution_error",
                        "completed": completed,
                        "failed": reference,
                        "error": str(exception),
                        "notes": notes,
                    }
                )

        return json.dumps(
            {
                "ok": True,
                "kind": "success",
                "completed": completed,
                "failed": None,
                "notes": notes,
            }
        )

    execute_steps.handle_validation_error = True

    @tool
    def switch_to_iframe(iframe_id: str):
        """ Switch iframe using iframe id """

    return [
        navigate_and_observe,
        observe_page,
        execute_steps,
        list_selected_credentials,
        switch_tab,
        request_user_input,
    ]
