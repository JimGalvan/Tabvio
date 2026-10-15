import logging
from dataclasses import replace
from uuid import UUID

from tabvio_agent.browser_agent.browser_agent import build_browser_agent
from tabvio_agent.browser_agent.context import AgentContext
from tabvio_agent.browser.session import BrowserSession
from tabvio_agent.runtime import LangChainAgentRuntime
from tabvio_agent.sensitive_input import SensitiveInputChannel

from tabvio.config import read_agent_settings
from tabvio.credentials.agent_provider import CredentialServiceProvider
from tabvio.credentials.service import CredentialService

logging.getLogger("dotenv.main").setLevel(logging.ERROR)


def build_agent_runtime(
        thread_id: UUID,
        user_id: UUID | None,
        credential_ids: tuple[UUID, ...] = (),
        credential_service: CredentialService | None = None,
        headless: bool | None = None,
):
    settings = read_agent_settings(thread_id)
    if headless is not None:
        settings = replace(settings, headless=headless)
    browser = BrowserSession(
        headless=settings.headless,
        trace_path=settings.trace_path,
        browser_channel=settings.browser_channel,
    )
    agent_context = AgentContext()
    sensitive_inputs = SensitiveInputChannel()
    credential_provider = None
    if credential_service and credential_ids and user_id:
        credential_provider = CredentialServiceProvider(credential_service, user_id, credential_ids)
    agent = build_browser_agent(browser, sensitive_inputs, settings, credential_provider=credential_provider)
    return LangChainAgentRuntime(agent, browser, agent_context, sensitive_inputs, thread_id)
