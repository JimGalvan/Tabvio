import asyncio
import json

from pydantic import ValidationError
from strands import tool
from strands.types.tools import ToolContext

from tabvio.agents.strands.browser_agent.context import AgentContext
from tabvio.agents.strands.browser_agent.schema import inline_references
from tabvio.agents.strands.browser_agent.steps import (
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
from tabvio.agents.strands.shared.events import AgentEventChannel
from tabvio.agents.strands.shared.verification import (
    describe_entered_code,
    explain_missing_code,
)
from tabvio.browser.session import BrowserSession
from tabvio.credentials.service import CredentialService
from tabvio.runs.sensitive_input import SensitiveInputChannel

STEP_PLAN_SCHEMA = inline_references(StepPlan.model_json_schema())


def build_browser_tools(
        browser: BrowserSession,
        channel: AgentEventChannel,
        agent_context: AgentContext,
        credential_service: CredentialService | None = None,
        sensitive_inputs: SensitiveInputChannel | None = None,
) -> list:
    sensitive_inputs = sensitive_inputs or SensitiveInputChannel()
    acknowledged_payment_surface: str | None = None
    pending_payment_observation = None
    browser_lock = asyncio.Lock()

    @tool(context=True)
    async def navigate_and_observe(url: str, tool_context: ToolContext) -> str:
        """Navigate to a URL and return the resulting page snapshot."""
        async with browser_lock:
            if pending_payment_observation is not None:
                return await finish_observation("", tool_context)
            channel.publish("browser.navigation.started", {"url": url})
            observation = await browser.attempt_navigate_and_observe(url)
            channel.publish("browser.navigation.completed", {"url": url})
            return await finish_observation(observation.page_state, tool_context)

    @tool(context=True)
    async def observe_page(tool_context: ToolContext) -> str:
        """Return the current page snapshot without navigating."""
        async with browser_lock:
            if pending_payment_observation is not None:
                return await finish_observation("", tool_context)
            observation = await browser.attempt_observe_page()
            channel.publish(
                "browser.observation", {"message": "Observed the current page"}
            )
            return await finish_observation(observation.page_state, tool_context)

    @tool(context=True)
    async def switch_tab(tab_id: str, tool_context: ToolContext) -> str:
        """Switch to the tab identified by a value such as `tab:1`."""
        async with browser_lock:
            if pending_payment_observation is not None:
                return await finish_observation("", tool_context)
            result = await browser.switch_tab(tab_id)
            channel.publish("browser.tab.changed", {"tab_id": tab_id})
            return await finish_observation(result, tool_context)

    @tool(context=True)
    def request_user_input(question: str, tool_context: ToolContext) -> str:
        """Pause and ask the user for required non-sensitive information."""
        channel.publish("input.required", {"question": question})
        answer = tool_context.interrupt("browser-question", reason={"question": question})
        return str(answer)

    @tool
    async def list_selected_credentials() -> str:
        """List safe metadata for credentials selected for this run."""
        if not agent_context.credential_ids:
            return "[]"
        if credential_service is None or agent_context.user_id is None:
            raise RuntimeError("Credential storage is not configured")

        metadata = await asyncio.to_thread(
            credential_service.require_selected,
            agent_context.credential_ids,
            agent_context.user_id,
        )

        described = []
        for item in metadata:
            preferred = []
            for method in item.preferred_verification:
                preferred.append(method.value)
            described.append(
                {
                    "id": str(item.id),
                    "name": item.name,
                    "allowed_domains": item.allowed_domains,
                    "preferred_verification": preferred,
                }
            )
        return json.dumps(described)

    async def finish_observation(page_state: str, tool_context: ToolContext) -> str:
        nonlocal pending_payment_observation

        detection = pending_payment_observation or browser.payment_detection_result
        if not detection.needs_handoff(acknowledged_payment_surface):
            return page_state

        # Remembered so the replay after the interrupt does not navigate again.
        pending_payment_observation = detection
        guard_payment_surface(tool_context, detection)
        pending_payment_observation = None
        observation = await browser.attempt_observe_page()
        return observation.page_state

    def guard_payment_surface(tool_context: ToolContext, detection=None) -> None:
        nonlocal acknowledged_payment_surface

        detection = detection or browser.payment_detection_result
        if not detection.needs_handoff(acknowledged_payment_surface):
            return

        question = (
            "This page can take a payment, so I have stopped before touching it. "
            "Take control of the browser, enter the payment details yourself, "
            "then tell me when I can continue."
        )
        channel.publish(
            "input.required",
            {"question": question, "payment_signals": detection.get_signals},
        )
        tool_context.interrupt(
            "browser-payment-handoff", reason={"question": question}
        )
        acknowledged_payment_surface = detection.fingerprint

    @tool(inputSchema={"json": STEP_PLAN_SCHEMA}, context=True)
    async def execute_steps(steps: list, tool_context: ToolContext) -> str:
        """Validate and execute browser steps, including credential and MFA steps."""
        async with browser_lock:
            guard_payment_surface(tool_context)

            try:
                plan = StepPlan.model_validate({"steps": steps})
                validate_plan(browser, plan.steps)
            except (ValidationError, ValueError) as exception:
                return json.dumps(
                    {
                        "ok": False,
                        "kind": "validation_error",
                        "completed": [],
                        "failed": None,
                        "error": str(exception),
                    }
                )

            completed = []
            notes = []
            for step in plan.steps:
                reference = step_reference(step)
                event_payload = step_event_payload(browser, step)
                channel.publish("browser.action.started", event_payload)
                try:
                    note = await run_step(step, tool_context)
                    if note is not None:
                        notes.append(note)
                except Exception as exception:
                    channel.publish(
                        "browser.action.failed",
                        {**event_payload, "error": str(exception)},
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

                completed.append(reference)
                channel.publish("browser.action.completed", event_payload)

            return json.dumps(
                {
                    "ok": True,
                    "kind": "success",
                    "completed": completed,
                    "failed": None,
                    "notes": notes,
                }
            )

    async def run_step(step: BrowserStep, tool_context: ToolContext) -> str | None:
        if isinstance(step, ClickStep):
            await browser.click(step.element_index)
        elif isinstance(step, FillStep):
            await browser.fill(step.element_index, step.value)
        elif isinstance(step, SelectStep):
            await browser.select(step.element_index, step.value)
        elif isinstance(step, PressStep):
            await browser.press(step.element_index, step.value)
        elif isinstance(step, CredentialFillStep):
            await fill_credential(step)
        elif isinstance(step, MfaCodeStep):
            return await request_mfa_code(step, tool_context)
        return None

    async def fill_credential(step: CredentialFillStep) -> None:
        if credential_service is None or agent_context.user_id is None:
            raise RuntimeError("Credential storage is not configured")
        if step.credential_id not in agent_context.credential_ids:
            raise ValueError("Credential is not selected for this run")

        secret = await asyncio.to_thread(
            credential_service.resolve_for_domain,
            step.credential_id,
            agent_context.user_id,
            browser.current_hostname,
        )
        value = secret.login if step.field == "login" else secret.password
        await browser.fill(step.element_index, value)
        del secret, value

    async def request_mfa_code(step: MfaCodeStep, tool_context: ToolContext) -> str:
        request = sensitive_inputs.begin(
            step.element_index, step.prompt, step.submit_element_index
        )
        channel.publish(
            "sensitive_input.required",
            {
                "request_id": str(request.id),
                "kind": request.kind,
                "prompt": request.prompt,
            },
        )
        result = tool_context.interrupt(
            "browser-mfa-code", reason={"request_id": str(request.id)}
        )
        sensitive_inputs.clear(request.id)

        if not isinstance(result, dict) or result.get("entered") is not True:
            raise RuntimeError(explain_missing_code(result))
        return describe_entered_code(result)

    return [
        navigate_and_observe,
        observe_page,
        execute_steps,
        list_selected_credentials,
        switch_tab,
        request_user_input,
    ]
