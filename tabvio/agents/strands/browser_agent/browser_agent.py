from pathlib import Path

from strands import Agent, AgentSkills

from tabvio.agents.strands.browser_agent.context import AgentContext
from tabvio.agents.strands.browser_agent.prompts import SYSTEM_PROMPT, TODO_INSTRUCTIONS
from tabvio.agents.strands.browser_agent.todos import TodoPromptHook, build_write_todos
from tabvio.agents.strands.browser_agent.tools import build_browser_tools
from tabvio.agents.strands.page_navigator.page_navigator import build_page_navigator
from tabvio.agents.strands.shared.events import AgentEventChannel
from tabvio.agents.strands.shared.llm import build_strong_model
from tabvio.agents.strands.shared.telemetry import configure_telemetry
from tabvio.browser.session import BrowserSession
from tabvio.credentials.service import CredentialService
from tabvio.runs.sensitive_input import SensitiveInputChannel

SKILLS_PATH = Path(__file__).resolve().parent / "skills"


def build_browser_agent(
        browser_session: BrowserSession,
        channel: AgentEventChannel,
        agent_context: AgentContext,
        sensitive_inputs: SensitiveInputChannel,
        credential_service: CredentialService | None = None,
) -> Agent:
    configure_telemetry()

    tools = build_browser_tools(
        browser_session,
        channel,
        agent_context,
        credential_service=credential_service,
        sensitive_inputs=sensitive_inputs,
    )
    tools.append(build_page_navigator(browser_session))
    tools.append(build_write_todos(channel))

    base_prompt = f"{SYSTEM_PROMPT}\n{TODO_INSTRUCTIONS}"
    return Agent(
        model=build_strong_model(),
        system_prompt=base_prompt,
        tools=tools,
        plugins=[AgentSkills(skills=str(SKILLS_PATH))],
        hooks=[TodoPromptHook(base_prompt)],
        callback_handler=None,
    )
