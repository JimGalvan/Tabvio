from pathlib import Path

from strands import Agent, AgentSkills

from tabvio.agents.browser_agent.context import AgentContext
from tabvio.agents.browser_agent.prompts import SYSTEM_PROMPT
from tabvio.agents.strands.events import AgentEventChannel
from tabvio.agents.strands.llm import build_strong_model
from tabvio.agents.strands.page_navigator import build_page_navigator
from tabvio.agents.strands.tools import build_browser_tools
from tabvio.browser.session import BrowserSession
from tabvio.credentials.service import CredentialService
from tabvio.runs.sensitive_input import SensitiveInputChannel

SKILLS_PATH = Path(__file__).resolve().parents[1] / "browser_agent" / "skills"


def build_browser_agent(
        browser_session: BrowserSession,
        channel: AgentEventChannel,
        agent_context: AgentContext,
        sensitive_inputs: SensitiveInputChannel,
        credential_service: CredentialService | None = None,
) -> Agent:
    tools = build_browser_tools(
        browser_session,
        channel,
        agent_context,
        credential_service=credential_service,
        sensitive_inputs=sensitive_inputs,
    )
    tools.append(build_page_navigator(browser_session))

    return Agent(
        model=build_strong_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        plugins=[AgentSkills(skills=str(SKILLS_PATH))],
        callback_handler=None,
    )
