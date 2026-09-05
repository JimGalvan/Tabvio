import asyncio
import json
import time

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool
from langgraph.types import interrupt
from pydantic import BaseModel

from tabvio.agents.browser_agent.context import AgentContext
from tabvio.agents.shared.events import publish_custom_event
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

    page_load_detector_subagent = build_page_loader_detector_subagent()

    @tool
    async def get_text_in_viewport() -> str:
        """Return text visible in the current browser viewport."""
        return await browser.get_text_in_viewport()

    @tool
    async def navigate_and_observe(url: str) -> str:
        """Navigate to a URL and return the resulting page snapshot."""
        publish_custom_event("browser.navigation.started", {"url": url})

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

        publish_custom_event("browser.navigation.completed", {"url": url})
        return observation

    @tool
    async def observe_page() -> str:
        """Return the current page snapshot without navigating."""
        observation = await browser.attempt_observe_page()
        initial_hash_observation = Utils.hash_string(observation)
        delays = [0.5, 1, 2, 3]
        has_page_changed = False
        final_observation = ""
        for delay in delays:
            if has_page_changed:
                break

            time.sleep(delay)
            new_observation = await browser.attempt_observe_page()
            new_hash_observation = Utils.hash_string(new_observation)

            if new_hash_observation != initial_hash_observation:
                has_page_changed = True
                final_observation = new_observation

        publish_custom_event(
            "browser.observation", {"message": "Observed the current page"}
        )
        return final_observation

    @tool
    async def switch_tab(tab_id: str) -> str:
        """Switch to the tab identified by a value such as `tab:1`."""
        result = await browser.switch_tab(tab_id)
        publish_custom_event("browser.tab.changed", {"tab_id": tab_id})
        return result

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
                    await browser.fill(step.username_element_index, secret.login)
                    await browser.fill(step.password_element_index, secret.password)
                    del secret
                elif isinstance(step, MfaCodeStep):
                    request = sensitive_inputs.begin(step.element_index, step.prompt)
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
                    if not isinstance(result, dict) or result.get("entered") is not True:
                        raise RuntimeError("The verification code was not entered")
                    sensitive_inputs.clear(request.id)

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
                    }
                )

        return json.dumps(
            {"ok": True, "kind": "success", "completed": completed, "failed": None}
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
