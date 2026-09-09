import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from tabvio.agents.browser_agent.context import AgentContext
from tabvio.runs.sensitive_input import SensitiveInputChannel
from tabvio.agents.browser_agent.browser_agent import build_browser_agent
from tabvio.browser.session import BrowserSession
from tabvio.config import (
    TRACE_DIRECTORY,
    read_browser_channel_setting,
    read_browser_trace_setting,
)
from tabvio.credentials.service import CredentialService

logging.getLogger("dotenv.main").setLevel(logging.ERROR)


@dataclass
class AgentRuntime:
    agent: Any
    config: dict[str, dict[str, str]]
    browser: BrowserSession
    context: AgentContext
    sensitive_inputs: SensitiveInputChannel


def _trace_path_for_run(thread_id: UUID) -> Path | None:
    if not read_browser_trace_setting():
        return None
    return TRACE_DIRECTORY / f"{thread_id}.zip"


def build_agent_runtime(
        thread_id: UUID,
        user_id: UUID | None,
        credential_ids: tuple[UUID, ...] = (),
        credential_service: CredentialService | None = None,
        headless: bool = True,
) -> AgentRuntime:
    browser = BrowserSession(
        headless=headless,
        trace_path=_trace_path_for_run(thread_id),
        browser_channel=read_browser_channel_setting(),
    )
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
