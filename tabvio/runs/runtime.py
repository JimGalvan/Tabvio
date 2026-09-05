import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from tabvio.agent.context import AgentContext
from tabvio.agent.sensitive_input import SensitiveInputChannel
from tabvio.agents.browser_agent.browser_agent import build_browser_agent
from tabvio.browser.session import BrowserSession
from tabvio.credentials.service import CredentialService

logging.getLogger("dotenv.main").setLevel(logging.ERROR)


@dataclass
class AgentRuntime:
    agent: Any
    config: dict[str, dict[str, str]]
    browser: BrowserSession
    context: AgentContext
    sensitive_inputs: SensitiveInputChannel


def build_agent_runtime(
        thread_id: UUID,
        user_id: UUID | None,
        credential_ids: tuple[UUID, ...] = (),
        credential_service: CredentialService | None = None,
        headless: bool = True,
) -> AgentRuntime:
    browser = BrowserSession(headless=headless)
    agent_context = AgentContext(user_id=user_id, credential_ids=credential_ids)
    sensitive_inputs = SensitiveInputChannel()
    agent = build_browser_agent(
        browser,
        sensitive_inputs,
        credential_service=credential_service,
    )
    config = {"configurable": {"thread_id": str(thread_id)}}
    return AgentRuntime(
        agent=agent,
        config=config,
        browser=browser,
        context=agent_context,
        sensitive_inputs=sensitive_inputs,
    )
