import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import ModelFallbackMiddleware, ModelRetryMiddleware
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from tabvio.agent.context import AgentContext
from tabvio.agent.llm import strong_model, model
from tabvio.agent.prompts import SYSTEM_PROMPT
from tabvio.agent.sensitive_input import SensitiveInputChannel
from tabvio.agent.subagents import build_page_navigator
from tabvio.browser.session import BrowserSession
from tabvio.browser.tools import build_browser_tools
from tabvio.credentials.service import CredentialService


def build_browser_agent(
        credential_service: CredentialService | None = None,
        headless: bool = True,
):
    agent_files = Path(__file__).resolve().parent / "files"

    browser_session = BrowserSession(headless=headless)
    sensitive_inputs = SensitiveInputChannel()
    backend = FilesystemBackend(root_dir=agent_files, virtual_mode=True)
    tools = build_browser_tools(
        browser_session,
        credential_service=credential_service,
        sensitive_inputs=sensitive_inputs,
    )
    page_navigator_subagent = build_page_navigator(browser_session)

    agent = create_deep_agent(
        model=strong_model,
        backend=backend,
        checkpointer=InMemorySaver(),
        system_prompt=SYSTEM_PROMPT,
        skills=["/skills"],
        tools=tools,
        subagents=[page_navigator_subagent],
        middleware=[
            ModelRetryMiddleware(
                max_retries=3,
                backoff_factor=2.0,
                initial_delay=1.0,
            ),
            ModelFallbackMiddleware(strong_model),
        ],
        context_schema=AgentContext,
    )
    return agent
