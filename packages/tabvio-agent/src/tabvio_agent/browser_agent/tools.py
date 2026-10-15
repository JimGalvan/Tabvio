import asyncio
import json

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from pydantic import ValidationError

from tabvio_agent.browser_agent.constants import PAGE_TEXT_CHARACTER_LIMIT
from tabvio_agent.browser_agent.context import AgentContext
from tabvio_agent.browser_agent.payment_summary import generate_payment_summary
from tabvio_agent.browser_agent.schema_utils import SchemaUtils
from tabvio_agent.browser_agent.steps import (
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
from tabvio_agent.credentials import CredentialProvider
from tabvio_agent.settings import AgentSettings
from tabvio_agent.shared.events import publish_custom_event
from tabvio_agent.shared.verification import (
    describe_entered_code,
    explain_missing_code,
)
from tabvio_agent.browser.session import BrowserSession
from tabvio_agent.sensitive_input import SensitiveInputChannel

STEP_PLAN_SCHEMA = SchemaUtils.inline_references(StepPlan.model_json_schema())


def build_browser_tools(
        browser: BrowserSession,
        credential_provider: CredentialProvider | None = None,
        sensitive_inputs: SensitiveInputChannel | None = None,
        settings: AgentSettings = AgentSettings(),
) -> list[BaseTool]:
    sensitive_inputs = sensitive_inputs or SensitiveInputChannel()
    acknowledged_payment_surface: str | None = None
    pending_payment_observation = None
    pending_payment_question: str | None = None
    browser_lock = asyncio.Lock()

    @tool
    async def navigate_and_observe(url: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Navigate to a URL and return the resulting page snapshot."""
        async with browser_lock:
            if pending_payment_observation is not None:
                return await finish_observation("", runtime)
            publish_custom_event("browser.navigation.started", {"url": url})
            observation = await browser.attempt_navigate_and_observe(url)
            publish_custom_event("browser.navigation.completed", {"url": url})
            return await finish_observation(observation.page_state, runtime)

    @tool
    async def observe_page(runtime: ToolRuntime[AgentContext]) -> str:
        """Return the current page snapshot without navigating."""
        async with browser_lock:
            if pending_payment_observation is not None:
                return await finish_observation("", runtime)
            observation = await browser.attempt_observe_page()
            publish_custom_event(
                "browser.observation", {"message": "Observed the current page"}
            )
            return await finish_observation(observation.page_state, runtime)

    @tool
    async def read_page_text() -> str:
        """Return the full text of the current page or selected frame, for reading values an observation shortened."""
        async with browser_lock:
            page_text = await browser.get_page_text()
        if len(page_text) <= PAGE_TEXT_CHARACTER_LIMIT:
            return page_text
        omitted = len(page_text) - PAGE_TEXT_CHARACTER_LIMIT
        return f"{page_text[:PAGE_TEXT_CHARACTER_LIMIT]}…[{omitted} chars omitted]"

    @tool
    async def switch_tab(tab_id: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Switch to the tab identified by a value such as `tab:1`."""
        async with browser_lock:
            if pending_payment_observation is not None:
                return await finish_observation("", runtime)
            result = await browser.switch_tab(tab_id)
            publish_custom_event("browser.tab.changed", {"tab_id": tab_id})
            return await finish_observation(result, runtime)

    @tool
    def request_user_input(question: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Pause and ask the user for required non-sensitive information."""
        publish_custom_event("input.required", {"question": question})
        answer = interrupt({"kind": "question", "question": question})
        return str(answer)

    @tool
    async def list_selected_credentials(runtime: ToolRuntime[AgentContext]) -> str:
        """List safe metadata for credentials selected for this run."""
        if not credential_provider:
            return "[]"
        metadata = await credential_provider.list_selected()

        described = []
        for item in metadata:
            described.append(
                {
                    "id": str(item.id),
                    "name": item.name,
                    "allowed_domains": item.allowed_domains,
                    "available_fields": item.available_fields,
                    "preferred_verification": item.preferred_verification,
                }
            )
        return json.dumps(described)

    async def finish_observation(page_state: str, runtime: ToolRuntime[AgentContext]) -> str:
        nonlocal pending_payment_observation

        detection = pending_payment_observation or browser.payment_detection_result
        if not detection.needs_handoff(acknowledged_payment_surface):
            return page_state

        pending_payment_observation = detection
        await guard_payment_surface(runtime, detection, page_state)
        pending_payment_observation = None
        observation = await browser.attempt_observe_page()
        return observation.page_state

    async def guard_payment_surface(
            runtime: ToolRuntime[AgentContext], detection=None, page_state: str = ""
    ) -> None:
        nonlocal acknowledged_payment_surface, pending_payment_question

        detection = detection or browser.payment_detection_result
        if not detection.needs_handoff(acknowledged_payment_surface):
            return

        if pending_payment_question is None:
            summary = await generate_payment_summary(runtime.state, page_state, settings)
            handoff = (
                "This page can take a payment, so I have stopped before touching it. "
                "Take control of the browser, enter the payment details yourself, "
                "then tell me when I can continue."
            )
            pending_payment_question = f"{summary}\n\n{handoff}" if summary else handoff
        question = pending_payment_question
        publish_custom_event(
            "input.required",
            {"question": question, "payment_signals": detection.get_signals},
        )
        interrupt({"kind": "payment_handoff", "question": question})
        acknowledged_payment_surface = detection.fingerprint
        pending_payment_question = None

    @tool(args_schema=STEP_PLAN_SCHEMA)
    async def execute_steps(steps: list, runtime: ToolRuntime[AgentContext]) -> str:
        """Validate and execute browser steps, including credential and MFA steps."""
        async with browser_lock:
            await guard_payment_surface(runtime)

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
                publish_custom_event("browser.action.started", event_payload)
                try:
                    note = await run_step(step, runtime)
                    if note is not None:
                        notes.append(note)
                except GraphInterrupt:
                    raise
                except Exception as exception:
                    publish_custom_event(
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
                publish_custom_event("browser.action.completed", event_payload)

            return json.dumps(
                {
                    "ok": True,
                    "kind": "success",
                    "completed": completed,
                    "failed": None,
                    "notes": notes,
                }
            )

    async def run_step(step: BrowserStep, runtime: ToolRuntime[AgentContext]) -> str | None:
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
            return await request_mfa_code(step, runtime)
        return None

    async def fill_credential(step: CredentialFillStep) -> None:
        if not credential_provider:
            raise RuntimeError("Credential storage is not configured")
        secret = await credential_provider.resolve(step.credential_id, browser.current_hostname)
        value = getattr(secret, step.field)
        if not value:
            raise ValueError(f"The selected credential has no saved {step.field} field")
        await browser.fill(step.element_index, value)
        del secret, value

    async def request_mfa_code(step: MfaCodeStep, runtime: ToolRuntime[AgentContext]) -> str:
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
        result = interrupt({"kind": request.kind, "request_id": str(request.id)})
        sensitive_inputs.clear(request.id)

        if not isinstance(result, dict) or result.get("entered") is not True:
            raise RuntimeError(explain_missing_code(result))
        return describe_entered_code(result)

    return [
        navigate_and_observe,
        observe_page,
        read_page_text,
        execute_steps,
        list_selected_credentials,
        switch_tab,
        request_user_input,
    ]
